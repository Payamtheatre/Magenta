import numpy as np

import tensorflow.compat.v1 as tf

FLAGS = tf.app.flags.FLAGS

if FLAGS.using_plaidml:
    import keras
else:
    import tensorflow.keras as keras


class DataGenerator(keras.utils.Sequence):
    'Generates data for Keras'
    def __init__(self, dataset, batch_size, steps_per_epoch, shuffle=False):
        'Initialization'
        self.dataset = dataset
        self.batch_size = batch_size
        self.steps_per_epoch = steps_per_epoch
        self.shuffle = shuffle
        self.on_epoch_end()

    def __len__(self):
        'Denotes the number of batches per epoch'
        return self.steps_per_epoch

    def __getitem__(self, index):
        'Generate one batch of data'

        x, y = ([t.numpy() for t in tensors] for tensors in next(iter(self.dataset)))
        return x, y

    def on_epoch_end(self):
        'Updates indexes after each epoch'
        if self.shuffle:
            np.random.shuffle(np.arange(self.steps_per_epoch * self.batch_size))