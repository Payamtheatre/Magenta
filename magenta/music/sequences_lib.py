# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Defines sequence of notes objects for creating datasets."""

import copy

# internal imports

from magenta.music import constants
from magenta.protobuf import music_pb2

# Set the quantization cutoff.
# Note events before this cutoff are rounded down to nearest step. Notes
# above this cutoff are rounded up to nearest step. The cutoff is given as a
# fraction of a step.
# For example, with quantize_cutoff = 0.75 using 0-based indexing,
# if .75 < event <= 1.75, it will be quantized to step 1.
# If 1.75 < event <= 2.75 it will be quantized to step 2.
# A number close to 1.0 gives less wiggle room for notes that start early,
# and they will be snapped to the previous step.
QUANTIZE_CUTOFF = 0.5

# Shortcut to chord symbol text annotation type.
CHORD_SYMBOL = music_pb2.NoteSequence.TextAnnotation.CHORD_SYMBOL


class BadTimeSignatureException(Exception):
  pass


class MultipleTimeSignatureException(Exception):
  pass


class MultipleTempoException(Exception):
  pass


class NegativeTimeException(Exception):
  pass


def extract_subsequence(sequence, start_time, end_time):
  """Extracts a subsequence from a NoteSequence.

  Notes starting before `start_time` are not included. Notes ending after
  `end_time` are truncated.

  Args:
    sequence: The NoteSequence to extract a subsequence from.
    start_time: The float time in seconds to start the subsequence.
    end_time: The float time in seconds to end the subsequence.

  Returns:
    A new NoteSequence that is a subsequence of `sequence` in the specified time
    range.
  """
  subsequence = music_pb2.NoteSequence()
  subsequence.CopyFrom(sequence)
  del subsequence.notes[:]
  for note in sequence.notes:
    if note.start_time < start_time or note.start_time >= end_time:
      continue
    new_note = subsequence.notes.add()
    new_note.CopyFrom(note)
    new_note.end_time = min(note.end_time, end_time)
  subsequence.total_time = min(sequence.total_time, end_time)
  return subsequence


def _is_power_of_2(x):
  return x and not x & (x - 1)


def steps_per_bar_in_quantized_sequence(note_sequence):
  """Calculates steps per bar in a NoteSequence that has been quantized.

  Args:
    note_sequence: the NoteSequence to examine.

  Returns:
    Steps per bar as a floating point number.
  """
  assert note_sequence.quantization_info.steps_per_quarter > 0

  quarters_per_beat = 4.0 / note_sequence.time_signatures[0].denominator
  quarters_per_bar = (quarters_per_beat *
                      note_sequence.time_signatures[0].numerator)
  steps_per_bar_float = (note_sequence.quantization_info.steps_per_quarter *
                         quarters_per_bar)
  return steps_per_bar_float


def quantize_note_sequence(note_sequence, steps_per_quarter):
  """Quantize a NoteSequence proto.

  The input NoteSequence is copied and quantization-related fields are
  populated.

  A note's start and end time are snapped to a nearby quantized step. See
  the comments above `QUANTIZE_CUTOFF` for details.

  Args:
    note_sequence: A music_pb2.NoteSequence protocol buffer.
    steps_per_quarter: Each quarter note of music will be divided into this
        many quantized time steps.

  Returns:
    A copy of the original NoteSequence, with quantized times added.

  Raises:
    MultipleTimeSignatureException: If there is a change in time signature
        in `note_sequence`.
    MultipleTempoException: If there is a change in tempo in `note_sequence`.
    BadTimeSignatureException: If the time signature found in `note_sequence`
        has a denominator which is not a power of 2.
    NegativeTimeException: If a note or chord occurs at a negative time.
  """
  qns = copy.deepcopy(note_sequence)

  qns.quantization_info.steps_per_quarter = steps_per_quarter

  if qns.time_signatures:
    time_signatures = sorted(qns.time_signatures, key=lambda ts: ts.time)
    # There is an implicit 4/4 time signature at 0 time. So if the first time
    # signature is something other than 4/4 and it's at a time other than 0,
    # that's an implicit time signature change.
    if time_signatures[0].time != 0 and not (
        time_signatures[0].numerator == 4 and
        time_signatures[0].denominator == 4):
      raise MultipleTimeSignatureException(
          'NoteSequence has an implicit change from initial 4/4 time '
          'signature.')

    for time_signature in time_signatures[1:]:
      if (time_signature.numerator != qns.time_signatures[0].numerator or
          time_signature.denominator != qns.time_signatures[0].denominator):
        raise MultipleTimeSignatureException(
            'NoteSequence has at least one time signature change.')

    # Make it clear that there is only 1 time signature and it starts at the
    # beginning.
    qns.time_signatures[0].time = 0
    del qns.time_signatures[1:]
  else:
    time_signature = qns.time_signatures.add()
    time_signature.numerator = 4
    time_signature.denominator = 4
    time_signature.time = 0

  if not _is_power_of_2(qns.time_signatures[0].denominator):
    raise BadTimeSignatureException(
        'Denominator is not a power of 2. Time signature: %d/%d' %
        (qns.time_signatures[0].numerator, qns.time_signatures[0].denominator))

  if qns.tempos:
    tempos = sorted(qns.tempos, key=lambda t: t.time)
    # There is an implicit 120.0 qpm tempo at 0 time. So if the first tempo is
    # something other that 120.0 and it's at a time other than 0, that's an
    # implicit tempo change.
    if tempos[0].time != 0 and (
        tempos[0].qpm != constants.DEFAULT_QUARTERS_PER_MINUTE):
      raise MultipleTempoException(
          'NoteSequence has an implicit tempo change from initial 120.0 qpm')

    for tempo in tempos[1:]:
      if tempo.qpm != qns.tempos[0].qpm:
        raise MultipleTempoException(
            'NoteSequence has at least one tempo change.')
    # Make it clear that there is only 1 tempo and it starts at the beginning.
    qns.tempos[0].time = 0
    del qns.tempos[1:]
  else:
    tempo = qns.tempos.add()
    tempo.qpm = constants.DEFAULT_QUARTERS_PER_MINUTE
    tempo.time = 0

  # Compute quantization steps per second.
  steps_per_second = steps_per_quarter * qns.tempos[0].qpm / 60.0

  quantize = lambda x: int(x + (1 - QUANTIZE_CUTOFF))

  qns.total_quantized_steps = quantize(qns.total_time * steps_per_second)

  for note in qns.notes:
    # Quantize the start and end times of the note.
    note.quantized_start_step = quantize(note.start_time * steps_per_second)
    note.quantized_end_step = quantize(note.end_time * steps_per_second)
    if note.quantized_end_step == note.quantized_start_step:
      note.quantized_end_step += 1

    # Do not allow notes to start or end in negative time.
    if note.quantized_start_step < 0 or note.quantized_end_step < 0:
      raise NegativeTimeException(
          'Got negative note time: start_step = %s, end_step = %s' %
          (note.quantized_start_step, note.quantized_end_step))

    # Extend quantized sequence if necessary.
    if note.quantized_end_step > qns.total_quantized_steps:
      qns.total_quantized_steps = note.quantized_end_step

  # Also quantize chord symbol annotations.
  for annotation in qns.text_annotations:
    # Quantize the chord time, disallowing negative time.
    annotation.quantized_step = quantize(annotation.time * steps_per_second)
    if annotation.quantized_step < 0:
      raise NegativeTimeException(
          'Got negative chord time: step = %s' % annotation.quantized_step)

  return qns
