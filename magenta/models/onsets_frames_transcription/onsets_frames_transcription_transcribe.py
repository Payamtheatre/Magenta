# Copyright 2020 The Magenta Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Transcribe a recording of piano audio."""

from __future__ import absolute_import, division, print_function

import json

import tensorflow.compat.v1 as tf
from dotmap import DotMap
from magenta.models.onsets_frames_transcription.data import wav_to_spec_op

FLAGS = tf.app.flags.FLAGS

tf.app.flags.DEFINE_boolean('using_plaidml', False, 'Are we using plaidml')

tf.app.flags.DEFINE_string('model_id', None, 'Id to save the model as')

tf.app.flags.DEFINE_string('config', 'onsets_frames',
                           'Name of the config to use.')
tf.app.flags.DEFINE_string('model_dir', None,
                           'Path to look for acoustic checkpoints.')
tf.app.flags.DEFINE_string(
    'checkpoint_path', None,
    'Filename of the checkpoint to use. If not specified, will use the latest '
    'checkpoint')
tf.app.flags.DEFINE_string(
    'hparams',
    '{}',
    'A comma-separated list of `name=value` hyperparameter values.')
tf.app.flags.DEFINE_boolean(
    'load_audio_with_librosa', False,
    'Whether to use librosa for sampling audio (required for 24-bit audio)')
tf.app.flags.DEFINE_string(
    'transcribed_file_suffix', 'predicted',
    'Optional suffix to add to transcribed files.')
tf.app.flags.DEFINE_string(
    'log', 'INFO',
    'The threshold for what messages will be logged: '
    'DEBUG, INFO, WARN, ERROR, or FATAL.')

from magenta.models.onsets_frames_transcription import configs
from magenta.models.onsets_frames_transcription import data
from magenta.models.onsets_frames_transcription.model_util import ModelWrapper, ModelType
from magenta.music import midi_io
from magenta.music.protobuf import music_pb2


def run(argv, config_map, data_fn):
    """Create transcriptions."""
    tf.compat.v1.logging.set_verbosity(FLAGS.log)

    config = config_map[FLAGS.config]
    hparams = config.hparams
    # For this script, default to not using cudnn.
    hparams.update(json.loads(FLAGS.hparams))
    hparams = DotMap(hparams)
    hparams.use_cudnn = False
    hparams.model_id = FLAGS.model_id
    hparams.batch_size = 1
    hparams.truncated_length_secs = 0

    midi_model = ModelWrapper('./models', ModelType.MIDI, id=hparams.model_id, hparams=hparams)
    #midi_model.load_model(80.37, 83.94, 'weights-zero')
    #midi_model.load_model(71.11, 85.35, 'frame-weight-4')
    #midi_model.load_model(44.94, 86.05, '1-4-9-threshold')
    #midi_model.load_model(63.17, 89.81, '2-4-9-threshold')
    midi_model.load_model(66.89, 86.17, '3-4-9-threshold')


    for filename in argv[1:]:
        tf.compat.v1.logging.info('Starting transcription for %s...', filename)

        wav_data = tf.gfile.Open(filename, 'rb').read()
        spec = wav_to_spec_op(wav_data, hparams=hparams)

        # add "batch" and channel dims
        spec = tf.reshape(spec, (1, *spec.shape, 1))

        tf.compat.v1.logging.info('Running inference...')
        sequence_prediction = midi_model.predict_sequence(spec)
        #assert len(prediction_list) == 1

        #sequence_prediction = music_pb2.NoteSequence.FromString(sequence_prediction)

        midi_filename = filename + FLAGS.transcribed_file_suffix + '.midi'
        midi_io.sequence_proto_to_midi_file(sequence_prediction, midi_filename)

        tf.compat.v1.logging.info('Transcription written to %s.', midi_filename)


def main(argv):
    run(argv, config_map=configs.CONFIG_MAP, data_fn=data.provide_batch)


def console_entry_point():
    tf.app.run(main)


if __name__ == '__main__':
    console_entry_point()
