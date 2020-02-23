from magenta.common import flatten_maybe_padded_sequences, tf_utils

import tensorflow.compat.v1 as tf

FLAGS = tf.app.flags.FLAGS

if FLAGS.using_plaidml:
    import keras.backend as K
else:
    import tensorflow.keras.backend as K


def log_loss_wrapper(class_weighing):
    def log_loss_wrapper_fn(label_true, label_predicted):
        return tf.reduce_mean(tf_utils.log_loss(K.flatten(label_true), K.flatten(label_predicted), class_weighing=class_weighing))

    return log_loss_wrapper_fn


def log_loss_flattener(labels, predictions):
    return tf.reduce_mean(tf_utils.log_loss(K.flatten(labels), K.flatten(predictions)))
