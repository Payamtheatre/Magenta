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

r"""Beam job for creating tfrecord files from datasets.
Expects a CSV with the following fields: audio_filename, midi_filename, split
Usage:
onsets_frames_transcription_create_tfrecords \
  --csv="/path/to/dataset.csv" \
  --output_directory="/path/to/output" \
  --num_shards="0" \
  --wav_dir="/path/to/dataset/audio" \
  --midi_dir="/path/to/dataset/midi" \
  --expected_splits="train,validation,test"
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import collections
import copy
import csv
import os

from absl import app
from absl import flags
from absl import logging

import apache_beam as beam
from apache_beam.metrics import Metrics
from magenta.models.onsets_frames_transcription import audio_label_data_utils
from magenta.music import midi_io
from magenta.music.protobuf import music_pb2
import tensorflow.compat.v1 as tf

FLAGS = flags.FLAGS

flags.DEFINE_string('base', None, 'Base path')
flags.DEFINE_string('output_directory', None, 'Path to output_directory')
flags.DEFINE_string('wav_dir', None, 'Directory for wav files.')
flags.DEFINE_string('midi_dir', None, 'Directory for midi files.')
flags.DEFINE_integer('num_shards', 0, 'number of output shards')
flags.DEFINE_string('expected_splits', 'train,validation,test',
                    'Comma separated list of expected splits.')
tf.app.flags.DEFINE_integer('min_length', 5, 'minimum length for a segment')
tf.app.flags.DEFINE_integer('max_length', 20, 'maximum length for a segment')
tf.app.flags.DEFINE_integer('sample_rate', 16000,
                            'sample_rate of the output files')
flags.DEFINE_boolean(
    'add_wav_glob', False,
    'If true, will add * to end of wav paths and use all matching files.')
flags.DEFINE_list(
    'pipeline_options', '--runner=DirectRunner',
    'A comma-separated list of command line arguments to be used as options '
    'for the Beam Pipeline.')


class CreateExampleDoFn(beam.DoFn):
    """Splits wav and midi files for the dataset."""

    def __init__(self, base, add_wav_glob,
                 *unused_args, **unused_kwargs):
        self._base = base
        self._add_wav_glob = add_wav_glob
        super(CreateExampleDoFn, self).__init__(*unused_args, **unused_kwargs)

    def process(self, paths):
        wav_path, midi_path = paths

        if midi_path:
            base_ns = midi_io.midi_file_to_note_sequence(midi_path)
            base_ns.filename = midi_path
        else:
            base_ns = music_pb2.NoteSequence()

        logging.info('Creating Example %s:%s', midi_path, wav_path)
        wav_data = tf.io.gfile.GFile(wav_path, 'rb').read()

        ns = copy.deepcopy(base_ns)

        # Use base names.
        ns.id = '%s:%s' % (wav_path,
                           midi_path)

        Metrics.counter('create_example', 'read_midi_wav').inc()

        if FLAGS.max_length > 0:
            split_examples = audio_label_data_utils.process_record(
                wav_data,
                ns,
                ns.id,
                min_length=FLAGS.min_length,
                max_length=FLAGS.max_length,
                sample_rate=FLAGS.sample_rate,
                load_audio_with_librosa=False)

            for example in split_examples:
                Metrics.counter('split_wav', 'split_example').inc()
                yield example
        else:

            example = audio_label_data_utils.create_example(ns.id, ns, wav_data)

            Metrics.counter('create_example', 'created_example').inc()
            yield example


def main(argv):
    del argv

    flags.mark_flags_as_required(['output_directory'])

    tf.io.gfile.makedirs(FLAGS.output_directory)

    splits = collections.defaultdict(list)
    for split in FLAGS.expected_splits.split(','):
        split_base = FLAGS.base + split
        wavs = tf.io.gfile.glob(split_base + FLAGS.wav_dir)
        midis = tf.io.gfile.glob(split_base + FLAGS.midi_dir)
        splits[split] = list(zip(wavs, midis))

    if sorted(splits.keys()) != sorted(FLAGS.expected_splits.split(',')):
        raise ValueError('Got unexpected set of splits: %s' % splits.keys())

    pipeline_options = beam.options.pipeline_options.PipelineOptions(
        FLAGS.pipeline_options)
    with beam.Pipeline(options=pipeline_options) as p:
        for split in splits:
            split_p = p | 'prepare_split_%s' % split >> beam.Create(splits[split])
            split_p |= 'create_examples_%s' % split >> beam.ParDo(
                CreateExampleDoFn(FLAGS.base + split, FLAGS.add_wav_glob))
            split_p |= 'write_%s' % split >> beam.io.WriteToTFRecord(
                os.path.join(FLAGS.output_directory, '%s.tfrecord' % split),
                coder=beam.coders.ProtoCoder(tf.train.Example),
                num_shards=FLAGS.num_shards)


if __name__ == '__main__':
    app.run(main)
