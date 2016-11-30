"""A module for implementing interaction between MIDI and SequenceGenerators."""

import abc
import threading
import time

# internal imports
import tensorflow as tf

import magenta
from magenta.protobuf import generator_pb2
from magenta.protobuf import music_pb2


class MidiInteractionException(Exception):
  """Base class for exceptions in this module."""
  pass


# TODO(adarob): Move to sequence_utils.
def merge_sequence_notes(sequence_1, sequence_2):
  """Returns a new NoteSequence combining the notes from both inputs.

  All fields aside from `notes` and `total_time` are copied from the first
  input.

  Args:
    sequence_1: A NoteSequence to merge. All fields aside from `notes` and
        `total_time` are copied directly from this sequence in the merged
        sequence.
    sequence_2: A NoteSequence to merge.

  Returns:
    A new NoteSequence combining the notes from the input sequences.
  """
  merged_sequence = music_pb2.NoteSequence()
  merged_sequence.CopyFrom(sequence_1)
  merged_sequence.notes.extend(sequence_2.notes)
  merged_sequence.total_time = max(sequence_1.total_time, sequence_2.total_time)
  return merged_sequence


# TODO(adarob): Move to sequence_utils.
def filter_instrument(sequence, instrument, from_time=0):
  """Returns a new NoteSequence with notes from the given instrument removed.

  Only notes that start on or after `from_time` will be completely removed.
  Those that start before and end after `from_time` will be truncated to end
  at `from_time`.

  Args:
    sequence: The NoteSequence to created the filtered sequence from.
    instrument: The instrument number to remove notes of.
    from_time: The time on or after which to remove or truncate notes.

  Returns:
    A new NoteSequence with notes from the given instrument removed or truncated
    after `from_time`.
  """
  filtered_sequence = music_pb2.NoteSequence()
  filtered_sequence.CopyFrom(sequence)
  del filtered_sequence.notes[:]
  for note in sequence.notes:
    if note.instrument == instrument:
      if note.start_time >= from_time:
        continue
      if note.end_time >= from_time:
        note.end_time = from_time
    filtered_sequence.notes.add().CopyFrom(note)
  return filtered_sequence

def retime(sequence, delta_time):
  retimed_sequence = music_pb2.NoteSequence()
  retimed_sequence.CopyFrom(sequence)

  for note in retimed_sequence.notes:
    note.start_time += delta_time
    note.end_time += delta_time
  retimed_sequence.total_time += delta_time
  return retimed_sequence

def rezero(sequence, zero_time):
  rezeroed_sequence = music_pb2.NoteSequence()
  rezeroed_sequence.CopyFrom(sequence)
  if not sequence.notes:
    return rezeroed_sequence
  old_zero_time = min(n.start_time for n in sequence.notes)
  delta_time = zero_time - old_zero_time

  return retime(sequence, delta_time)

def temperature_from_control_value(
    val, min_temp=0.1, mid_temp=1.0, max_temp=2.0):
  """Computes the temperature from an 8-bit MIDI control value.

  Linearly interpolates between the middle temperature and an endpoint.

  Args:
    val: The MIDI control value in the range [0, 127] or None. If None, returns
        `mid_temp`.
    min_temp: The minimum temperature, which will be returned when `val` is 0.
    mid_temp: The middle temperature, which will be returned when `val` is 63
       or 64.
    max_temp: The maximum temperature, which will be returned when `val` is 127.

  Returns:
    A float temperature value based on the 8-bit MIDI control value.
  """
  if val is None:
    return mid_temp
  if val > 64:
    return mid_temp + (val - 64) * (max_temp - mid_temp) / 63
  elif val < 63:
    return min_temp + val * (mid_temp - min_temp) / 63
  else:
    return mid_temp


class MidiInteraction(threading.Thread):
  """Base class for handling interaction between MIDI and SequenceGenerator.

  Child classes will provided the "main loop" of an interactive session between
  a MidiHub used for MIDI I/O and sequences generated by a SequenceGenerator in
  their `run` methods.

  Should be started by calling `start` to launch in a separate thread.

  Args:
    midi_hub: The MidiHub to use for MIDI I/O.
    sequence_generators: A collection of SequenceGenerator objects.
    qpm: The quarters per minute to use for this interaction. May be overriden
       by control changes sent to `tempo_control_number`.
    generator_select_control_number: An optional MIDI control number whose
       value to use for selection a sequence generator from the collection.
       Must be provided if `sequence_generators` contains multiple
       SequenceGenerators.
    tempo_control_number: An optional MIDI control number whose value to use to
       determine the qpm for this interaction. On receipt of a control change,
       the qpm will be set to 60 more than the control change value.
    qpm: The quarters per minute to use for this interaction.
    generator_select_control_number: A MIDI control number in [0, 127] whose
       value to use for selection a sequence generator from the collection.
       Must be provided if `sequence_generators` contains multiple
       SequenceGenerators.

  Raises:
    ValueError: If `generator_select_control_number` is None and
        `sequence_generators` contains multiple SequenceGenerators.
  """
  _metaclass__ = abc.ABCMeta

  # Base QPM when set by a tempo control change.
  _BASE_QPM = 60

  def __init__(self, midi_hub, sequence_generators, qpm,
               generator_select_control_number=None, tempo_control_number=None):
    if generator_select_control_number is None and len(sequence_generators) > 1:
      raise ValueError(
          '`generator_select_control_number` cannot be None if there are '
          'multiple SequenceGenerators.')
    self._midi_hub = midi_hub
    self._sequence_generators = sequence_generators
    self._default_qpm = qpm
    self._generator_select_control_number = generator_select_control_number
    self._tempo_control_number = tempo_control_number

    # A signal to tell the main loop when to stop.
    self._stop_signal = threading.Event()
    super(MidiInteraction, self).__init__()

  @property
  def _sequence_generator(self):
    """Returns the SequenceGenerator selected by the current control value."""
    if len(self._sequence_generators) == 1:
      return self._sequence_generators[0]
    val = self._midi_hub.control_value(self._generator_select_control_number)
    val = 0 if val is None else val
    return self._sequence_generators[val % len(self._sequence_generators)]

  @property
  def _qpm(self):
    """Returns the qpm based on the current tempo control value."""
    if self._tempo_control_number is None:
      return self._default_qpm
    val = self._midi_hub.control_value(self._tempo_control_number)
    return self._default_qpm if val is None else val + self._BASE_QPM

  @abc.abstractmethod
  def run(self):
    """The main loop for the interaction.

    Must exit shortly after `self._stop_signal` is set.
    """
    pass

  def stop(self):
    """Stops the main loop, and blocks until the interaction is stopped."""
    self._stop_signal.set()
    self.join()


class CallAndResponseMidiInteraction(MidiInteraction):
  """Implementation of a MidiInteraction for real-time "call and response".

  Alternates between receiving input from the MidiHub ("call") and playing
  generated sequences ("response"). During the call stage, the input is captured
  and used to generate the response, which is then played back during the
  response stage.

  Args:
    midi_hub: The MidiHub to use for MIDI I/O.
    qpm: The quarters per minute to use for this interaction. May be overriden
       by control changes sent to `tempo_control_number`.
    generator_select_control_number: An optional MIDI control number whose
       value to use for selection a sequence generator from the collection.
       Must be provided if `sequence_generators` contains multiple
       SequenceGenerators.
    tempo_control_number: An optional MIDI control number whose value to use to
       determine the qpm for this interaction. On receipt of a control change,
       the qpm will be set to 60 more than the control change value.
    steps_per_quarter: The number of steps per quarter note.
    steps_per_bar: The number of steps in each bar/measure.
    phrase_bars: The optional number of bars in each phrase. `end_call_signal`
        must be provided if None.
    start_call_signal: The control change number to use as a signal to start the
       call phrase. If None, call will start immediately after response.
    end_call_signal: The optional midi_hub.MidiSignal to use as a signal to stop
        the call phrase at the end of the current bar. `phrase_bars` must be
        provided if None.
    temperature_control_number: The optional control change number to use for
        controlling temperature.
  """
  _INITIAL_PREDICTAHEAD_STEPS = 4
  _MIN_PREDICTAHEAD_STEPS = 1

  def __init__(self,
               midi_hub,
               sequence_generators,
               qpm,
               generator_select_control_number=None,
               steps_per_quarter=4,
               steps_per_bar=16,
               phrase_bars=None,
               start_call_signal=None,
               end_call_signal=None,
               temperature_control_number=None):
    super(CallAndResponseMidiInteraction, self).__init__(
        midi_hub, sequence_generators, qpm, generator_select_control_number)
    self._steps_per_bar = steps_per_bar
    self._steps_per_quarter = steps_per_quarter
    self._phrase_bars = phrase_bars
    self._start_call_signal = start_call_signal
    self._end_call_signal = end_call_signal
    self._temperature_control_number = temperature_control_number

  def run(self):
    """The main loop for a real-time call and response interaction."""

    # We measure time in units of steps.
    seconds_per_step = 60.0 / (self._qpm * self._steps_per_quarter)
    # Start time in steps from the epoch.
    start_steps = (time.time() + 1.0) // seconds_per_step

    # The number of steps before call stage ends to start generation of response
    # Will be automatically adjusted to be as small as possible while avoiding
    # late response starts.
    predictahead_steps = self._INITIAL_PREDICTAHEAD_STEPS

    # Call stage start in steps from the epoch.
    call_start_steps = start_steps

    while not self._stop_signal.is_set():
      if self._start_call_signal is not None:
        # Wait for start signal.
        self._midi_hub.wait_for_event(self._start_call_signal)
        # Check to see if a stop has been requested.
        if self._stop_signal.is_set():
          break

      # Call stage.

      # Start the metronome at the beginning of the call stage.
      self._midi_hub.start_metronome(
          self._qpm, call_start_steps * seconds_per_step)

      # Start a captor at the beginning of the call stage.
      captor = self._midi_hub.start_capture(
          self._qpm, call_start_steps * seconds_per_step)

      if self._phrase_bars is not None:
        # The duration of the call stage in steps.
        call_steps = self._phrase_bars * self._steps_per_bar
      else:
        # Wait for end signal.
        self._midi_hub.wait_for_event(self._end_call_signal)
        # The duration of the call stage in steps.
        # We end the call stage at the end of the next bar that is at least
        # `predicathead_steps` in the future.
        call_steps = time.time() // seconds_per_step - call_start_steps
        remaining_call_steps = -call_steps % self._steps_per_bar
        if remaining_call_steps < predictahead_steps:
          remaining_call_steps += self._steps_per_bar
        call_steps += remaining_call_steps

      # Set the metronome to stop at the appropriate time.
      self._midi_hub.stop_metronome(
          (call_steps + call_start_steps) * seconds_per_step,
          block=False)

      # Stop the captor at the appropriate time.
      capture_steps = call_steps - predictahead_steps
      captor.stop(stop_time=(
          (capture_steps + call_start_steps) * seconds_per_step))
      captured_sequence = captor.captured_sequence()

      # Check to see if a stop has been requested during capture.
      if self._stop_signal.is_set():
        break

      # Generate sequence.
      response_start_steps = call_steps + call_start_steps
      response_end_steps = 2 * call_steps + call_start_steps

      generator_options = generator_pb2.GeneratorOptions()
      generator_options.generate_sections.add(
          start_time=response_start_steps * seconds_per_step,
          end_time=response_end_steps * seconds_per_step)

      # Get current temperature setting.
      temperature = temperature_from_control_value(
          self._midi_hub.control_value(self._temperature_control_number))
      if temperature is not None:
        generator_options.args['temperature'].float_value = temperature

      tf.logging.debug('Generator Details: %s',
                       self._sequence_generator.details)
      tf.logging.debug('Bundle Details: %s',
                       self._sequence_generator.bundle_details)
      tf.logging.debug('Generator Options: %s', generator_options)

      # Generate response.
      response_sequence = self._sequence_generator.generate(
          captured_sequence, generator_options)

      # Check to see if a stop has been requested during generation.
      if self._stop_signal.is_set():
        break

      # Response stage.
      # Start response playback.
      self._midi_hub.start_playback(response_sequence)

      # Compute remaining time after generation before the response stage
      # starts, updating `predictahead_steps` appropriately.
      remaining_time = response_start_steps * seconds_per_step - time.time()
      if remaining_time > (predictahead_steps * seconds_per_step):
        predictahead_steps = max(self._MIN_PREDICTAHEAD_SEPS,
                                 response_start_steps - 1)
        tf.logging.info('Generator is ahead by %.3f seconds. '
                        'Decreasing `predictahead_steps` to %d.',
                        remaining_time, predictahead_steps)
      elif remaining_time < 0:
        predictahead_steps += 1
        tf.logging.warn('Generator is lagging by %.3f seconds. '
                        'Increasing `predictahead_steps` to %d.',
                        -remaining_time, predictahead_steps)

      call_start_steps = response_end_steps

  def stop(self):
    self._stop_signal.set()
    if self._start_call_signal is not None:
      self._midi_hub.wake_signal_waiters(self._start_call_signal)
    if self._end_call_signal is not None:
      self._midi_hub.wake_signal_waiters(self._end_call_signal)
    super(CallAndResponseMidiInteraction, self).stop()


class ExternalClockCallAndResponse(MidiInteraction):
  """Implementation of a MidiInteraction which follows external sync timing.

  Alternates between receiving input from the MidiHub ("call") and playing
  generated sequences ("response"). During the call stage, the input is captured
  and used to generate the response, which is then played back during the
  response stage.

  The call phrase is started when notes are received and ended by an external
  signal (`end_call_signal`) or after receiving no note events for a full tick.
  The response phrase is immediately generated and played. Its length is
  optionally determined by a control value set for `duration_control_number` or
  by the length of the call.

  Args:
    midi_hub: The MidiHub to use for MIDI I/O.
    qpm: The quarters per minute to use for this interaction. May be overriden
       by control changes sent to `tempo_control_number`.
    generator_select_control_number: An optional MIDI control number whose
       value to use for selection a sequence generator from the collection.
       Must be provided if `sequence_generators` contains multiple
       SequenceGenerators.
    tempo_control_number: An optional MIDI control number whose value to use to
       determine the qpm for this interaction. On receipt of a control change,
       the qpm will be set to 60 more than the control change value.
    clock_signal: A midi_hub.MidiSignal to use as a clock.
    end_call_signal: The optional midi_hub.MidiSignal to use as a signal to stop
        the call phrase at the end of the current tick.
    allow_overlap: A boolean specifying whether to allow the call to overlap
        with the response.
    min_listen_ticks_control_number: The optional control change number to use
        for controlling the minimum call phrase length in clock ticks.
    max_listen_ticks_control_number: The optional control change number to use
        for controlling the maximum call phrase length in clock ticks.
    response_ticks_control_number: The optional control change number to use for
        controlling the length of the response in clock ticks.
    temperature_control_number: The optional control change number to use for
        controlling temperature.
    state_control_number: The optinal control change number to use for sending
        state update control changes. The values are 0 for `IDLE`, 1 for
        `LISTENING`, and 2 for `RESPONDING`.
    """

  class State(object):
    """Class holding state value representations."""
    IDLE = 0
    LISTENING = 1
    RESPONDING = 2

    _STATE_NAMES = {
        IDLE: 'Idle', LISTENING: 'Listening', RESPONDING:'Responding'}

    @classmethod
    def to_string(cls, state):
      return cls._STATE_NAMES[state]


  def __init__(self,
               midi_hub,
               sequence_generators,
               qpm,
               generator_select_control_number,
               tempo_control_number,
               clock_signal,
               end_call_signal=None,
               panic_signal=None,
               allow_overlap=False,
               min_listen_ticks_control_number=None,
               max_listen_ticks_control_number=None,
               response_ticks_control_number=None,
               temperature_control_number=None,
               loop_control_number=None,
               state_control_number=None):
    super(ExternalClockCallAndResponse, self).__init__(
        midi_hub, sequence_generators, qpm, generator_select_control_number)
    self._clock_signal = clock_signal
    self._end_call_signal = end_call_signal
    self._panic_signal = panic_signal
    self._allow_overlap = allow_overlap
    self._min_listen_ticks_control_number = min_listen_ticks_control_number
    self._max_listen_ticks_control_number = max_listen_ticks_control_number
    self._response_ticks_control_number = response_ticks_control_number
    self._temperature_control_number = temperature_control_number
    self._loop_control_number = loop_control_number
    self._state_control_number = state_control_number
    # Event for signalling when to end a call.
    self._end_call = threading.Event()
    self._panic = threading.Event()

  def _update_state(self, state):
    """Logs and sends a control change with the state."""
    if self._state_control_number is not None:
      self._midi_hub.send_control_change(self._state_control_number, state)
    tf.logging.info('State: %s', self.State.to_string(state))

  def _end_call_callback(self, unused_captured_seq):
    """Method to use as a callback for setting the end call signal."""
    self._end_call.set()
    tf.logging.info('End call signal received.')

  def _panic_callback(self, unused_captured_seq):
    """Method to use as a callback for setting the panic signal."""
    self._panic.set()
    tf.logging.info('Panic signal received.')

  @property
  def _min_listen_ticks(self):
    """Returns the min listen ticks based on the current control value."""
    if self._min_listen_ticks_control_number is None:
      return 0
    val = self._midi_hub.control_value(
        self._min_listen_ticks_control_number)
    return 0 if val is None else val

  @property
  def _max_listen_ticks(self):
    """Returns the max listen ticks based on the current control value."""
    if self._max_listen_ticks_control_number is None:
      return float('inf')
    val = self._midi_hub.control_value(
        self._max_listen_ticks_control_number)
    return float('inf') if not val else val

  @property
  def _should_loop(self):
    return (self._loop_control_number and
            self._midi_hub.control_value(self._loop_control_number) == 127)

  def run(self):
    """The main loop for a real-time call and response interaction."""
    self._captor = self._midi_hub.start_capture(self._qpm, time.time())

    # Set callback for end call signal.
    if self._end_call_signal is not None:
      self._captor.register_callback(self._end_call_callback,
                                     signal=self._end_call_signal)
    if self._panic_signal is not None:
      self._captor.register_callback(self._panic_callback,
                                     signal=self._panic_signal)

    # Keep track of the end of the previous tick time.
    last_tick_time = time.time()

    # Keep track of the duration of a listen state.
    listen_ticks = 0

    response_sequence = music_pb2.NoteSequence()
    response_start_time = 0
    player = self._midi_hub.start_playback(
        response_sequence, allow_updates=True)

    for captured_sequence in self._captor.iterate(signal=self._clock_signal):
      if self._stop_signal.is_set():
        break
      if self._panic.is_set():
        response_sequence = music_pb2.NoteSequence()
        player.update_sequence(response_sequence)
        self._panic.clear()

      # Set to current QPM, since it might have changed.
      captured_sequence.tempos[0].qpm = self._qpm

      tick_time = captured_sequence.total_time
      last_end_time = (max(note.end_time for note in captured_sequence.notes)
                       if captured_sequence.notes else None)

      listen_ticks += 1

      if not captured_sequence.notes:
        # Reset captured sequence since we are still idling.
        if response_sequence.total_time <= tick_time:
          self._update_state(self.State.IDLE)
        if self._captor.start_time < tick_time:
          self._captor.start_time = tick_time
        self._end_call.clear()
        listen_ticks = 0
      elif (self._end_call.is_set() or
            last_end_time <= last_tick_time or
            listen_ticks >= self._max_listen_ticks):
        if listen_ticks < self._min_listen_ticks:
          tf.logging.info(
            'Input too short (%d vs %d). Skipping.',
            listen_ticks,
            self._min_listen_ticks)
          self._captor.start_time = tick_time
        else:
          # Create response and start playback.
          self._update_state(self.State.RESPONDING)

          capture_start_time = self._captor.start_time

          if last_end_time <= last_tick_time:
            # Move the sequence forward one tick in time.
            captured_sequence = retime(captured_sequence,
                                       tick_time - last_tick_time)
            capture_start_time += tick_time - last_tick_time

          # Compute duration of response.
          num_ticks = (
              self._midi_hub.control_value(self._response_ticks_control_number)
              if self._response_ticks_control_number is not None else None)
          if num_ticks:
            response_duration = num_ticks * (tick_time - last_tick_time)
          else:
            # Use capture duration.
            response_duration = tick_time - capture_start_time

          last_end_time <= last_tick_time

          # Generate sequence options.
          response_start_time = tick_time
          response_end_time = response_start_time + response_duration

          generator_options = magenta.protobuf.generator_pb2.GeneratorOptions()
          generator_options.input_sections.add(
              start_time=0,
              end_time=tick_time - capture_start_time)
          generator_options.generate_sections.add(
              start_time=response_start_time - capture_start_time,
              end_time=response_end_time - capture_start_time)

          # Get current temperature setting.
          temperature = temperature_from_control_value(
              self._midi_hub.control_value(self._temperature_control_number))
          if temperature is not None:
            generator_options.args['temperature'].float_value = temperature

          # Generate response.
          tf.logging.info(
              "Generating sequence using '%s' generator from bundle: %s",
              self._sequence_generator.details.id,
              self._sequence_generator.bundle_details.id)
          tf.logging.debug('Generator Details: %s',
                           self._sequence_generator.details)
          tf.logging.debug('Bundle Details: %s',
                           self._sequence_generator.bundle_details)
          tf.logging.debug('Generator Options: %s', generator_options)
          response_sequence = self._sequence_generator.generate(
              retime(captured_sequence, -capture_start_time),
              generator_options)
          response_sequence = retime(response_sequence, capture_start_time)
          response_sequence = magenta.music.extract_subsequence(
              response_sequence, response_start_time, response_end_time)
          # Start response playback. Specify the start_time to avoid stripping
          # initial events due to generation lag.
          player.update_sequence(
              response_sequence, start_time=response_start_time)

          # Optionally capture during playback.
          if self._allow_overlap:
            self._captor.start_time = response_start_time
          else:
            self._captor.start_time = response_end_time

        # Clear end signal.
        self._end_call.clear()
        listen_ticks = 0
      else:
        # Continue listening.
        self._update_state(self.State.LISTENING)

      # Potentially loop previous response.
      if (response_sequence.total_time <= tick_time and
          self._should_loop):
        response_sequence = retime(response_sequence,
                                   tick_time - response_start_time)
        response_start_time = tick_time
        player.update_sequence(response_sequence, start_time=tick_time)

      last_tick_time = tick_time

    player.stop()

  def stop(self):
    self._stop_signal.set()
    self._captor.stop()
    super(ExternalClockCallAndResponse, self).stop()
