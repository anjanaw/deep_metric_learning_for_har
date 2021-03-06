import keras
import numpy as np
import tensorflow as tf
from keras.layers import Input, Lambda, Dense, Conv1D, MaxPooling1D, Flatten
from keras.layers.merge import _Merge
from keras.layers.normalization import BatchNormalization
from keras.models import Model
import read

np.random.seed(1)
tf.set_random_seed(2)

samples_per_class = 5
classes_per_set = 5
feature_length = read.dct_length * 3 * 3
batch_size = 60
epochs = 10
k = 3


class MatchCosine(_Merge):
    def __init__(self, nway=5, n_samp=1, **kwargs):
        super(MatchCosine, self).__init__(**kwargs)
        self.eps = 1e-10
        self.nway = nway
        self.n_samp = n_samp

    def build(self, input_shape):
        if not isinstance(input_shape, list) or len(input_shape) != self.nway * self.n_samp + 2:
            raise ValueError(
                'A ModelCosine layer should be called on a list of inputs of length %d' % (self.nway * self.n_samp + 2))

    def call(self, inputs):
        self.nway = (len(inputs) - 2) / self.n_samp
        similarities = []

        targetembedding = inputs[-2]
        numsupportset = len(inputs) - 2
        for ii in range(numsupportset):
            supportembedding = inputs[ii]

            sum_support = tf.reduce_sum(tf.square(supportembedding), 1, keep_dims=True)
            supportmagnitude = tf.rsqrt(tf.clip_by_value(sum_support, self.eps, float("inf")))

            sum_query = tf.reduce_sum(tf.square(targetembedding), 1, keep_dims=True)
            querymagnitude = tf.rsqrt(tf.clip_by_value(sum_query, self.eps, float("inf")))

            dot_product = tf.matmul(tf.expand_dims(targetembedding, 1), tf.expand_dims(supportembedding, 2))
            dot_product = tf.squeeze(dot_product, [1])

            cosine_similarity = dot_product * supportmagnitude * querymagnitude
            similarities.append(cosine_similarity)

        similarities = tf.concat(axis=1, values=similarities)
        softmax_similarities = tf.nn.softmax(similarities)
        preds = tf.squeeze(tf.matmul(tf.expand_dims(softmax_similarities, 1), inputs[-1]))

        preds.set_shape((inputs[0].shape[0], self.nway))
        return preds

    def compute_output_shape(self, input_shape):
        input_shapes = input_shape
        return (input_shapes[0][0], self.nway)


def packslice(data_set):
    n_samples = samples_per_class * classes_per_set
    support_cacheX = []
    support_cacheY = []
    target_cacheY = []

    for _class in data_set:

        class_data = data_set[_class]
        class_data = np.array(class_data)
        class_data = np.expand_dims(class_data, 3)

        for item in class_data:
            slice_x = np.zeros((n_samples + 1, feature_length, 1))
            slice_y = np.zeros((n_samples,))
            pinds = np.random.permutation(n_samples)

            classes = []
            while _class not in classes:
                classes = np.random.choice(list(data_set.keys()), classes_per_set, False)
            ind = 0

            for j, cur_class in enumerate(classes):
                data_pack = data_set[cur_class]
                data_pack = np.array(data_pack)
                data_pack = np.expand_dims(data_pack, 3)
                example_inds = np.random.choice(len(data_pack), samples_per_class, False)

                for eind in example_inds:
                    slice_x[pinds[ind], :, :] = data_pack[eind]
                    slice_y[pinds[ind]] = j
                    ind += 1

                if _class == cur_class:
                    slice_x[n_samples, :, :] = item
                    target_y = j

            support_cacheX.append(slice_x)
            support_cacheY.append(keras.utils.to_categorical(slice_y, classes_per_set))
            target_cacheY.append(keras.utils.to_categorical(target_y, classes_per_set))

    return np.array(support_cacheX), np.array(support_cacheY), np.array(target_cacheY)


def create_train_instances(train_sets):
    support_X = None
    support_y = None
    target_y = None

    for user_id, train_feats in train_sets.items():
        _support_X, _support_y, _target_y = packslice(train_feats)

        if support_X is not None:
            support_X = np.concatenate((support_X, _support_X))
            support_y = np.concatenate((support_y, _support_y))
            target_y = np.concatenate((target_y, _target_y))
        else:
            support_X = _support_X
            support_y = _support_y
            target_y = _target_y

    print("Data shapes: ")
    print(support_X.shape)
    print(support_y.shape)
    print(target_y.shape)
    return [support_X, support_y, target_y]


def conv_embedding():
    _input = Input(shape=(feature_length,1))
    x = Conv1D(12, kernel_size=3, activation='relu')(_input)
    x = MaxPooling1D(pool_size=2)(x)
    x = BatchNormalization()(x)
    x = Flatten()(x)
    x = Dense(1200, activation='relu')(x)
    x = BatchNormalization()(x)
    return Model(inputs=_input, outputs=x, name='embedding')


feature_data = read.read()

test_ids = list(feature_data.keys())
for test_id in test_ids:
    _train_data, _test_data = read.split(feature_data, test_id)
    train_data = create_train_instances(_train_data)

    _train_data, _train_labels = read.flatten(_train_data)
    _test_data, _test_labels = read.flatten(_test_data)
    print(len(_train_data))
    _train_data = np.array(_train_data)
    _test_data = np.array(_test_data)
    _train_data = np.expand_dims(_train_data, 3)
    _test_data = np.expand_dims(_test_data, 3)

    numsupportset = samples_per_class * classes_per_set
    input1 = Input((numsupportset + 1, feature_length, 1))

    modelinputs = []
    base_network = conv_embedding()
    for lidx in range(numsupportset):
        modelinputs.append(base_network(Lambda(lambda x: x[:, lidx, :, :])(input1)))
    targetembedding = base_network(Lambda(lambda x: x[:, -1, :, :])(input1))
    modelinputs.append(targetembedding)
    supportlabels = Input((numsupportset, classes_per_set))
    modelinputs.append(supportlabels)
    knnsimilarity = MatchCosine(nway=classes_per_set, n_samp=samples_per_class)(modelinputs)

    model = Model(inputs=[input1, supportlabels], outputs=knnsimilarity)
    model.compile(optimizer='adam', loss='categorical_crossentropy', metrics=['accuracy'])
    model.fit([train_data[0], train_data[1]], train_data[2], epochs=epochs, batch_size=batch_size, verbose=1)

    _train_preds = base_network.predict(_train_data)
    _test_preds = base_network.predict(_test_data)

    acc = read.cos_knn(k, _test_preds, _test_labels, _train_preds, _train_labels)
    result = 'mn_conv, 3nn,' + str(test_id) + ',' + str(acc)
    print(result)
    read.write_data('mn_conv.csv', result)

