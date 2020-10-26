import os
import copy
from collections import OrderedDict
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.hub import load_state_dict_from_url

from .utils import register_model, BaseModule
from .resnet_backbone import ResNetFeature, BasicBlock, Bottleneck, model_urls


@register_model('PlainSemiResNetClassifier')
class PlainSemiResNetClassifier(BaseModule):

    name = 'PlainSemiResNetClassifier'

    def __init__(self, num_cls=10, weights_init='ImageNet', num_layers=18, init_feat_only=True, T=None, alpha=None):
        super(PlainSemiResNetClassifier, self).__init__()
        self.num_cls = num_cls
        self.num_layers = num_layers
        self.feature = None
        self.classifier = None
        self.criterion_cls = None
        self.best_weights = None
        self.feature_dim = None
        self.T = T
        self.alpha = alpha

        # Model setup and weights initialization
        self.setup_net()

        if weights_init == 'ImageNet':
            self.load(model_urls['resnet{}'.format(num_layers)], feat_only=init_feat_only)
        elif os.path.exists(weights_init):
            self.load(weights_init, feat_only=init_feat_only)
        elif weights_init != 'ImageNet' and not os.path.exists(weights_init):
            raise NameError('Initial weights not exists {}.'.format(weights_init))

        # Criteria setup
        self.setup_critera()

    def setup_net(self):

        kwargs = {}

        if self.num_layers == 18:
            block = BasicBlock
            layers = [2, 2, 2, 2]
        elif self.num_layers == 50:
            block = Bottleneck
            layers = [3, 4, 6, 3]
        elif self.num_layers == 152:
            block = Bottleneck
            layers = [3, 8, 36, 3]
        else:
            raise Exception('ResNet Type not supported.')

        self.feature = ResNetFeature(block, layers, **kwargs)
        self.classifier = nn.Linear(512 * block.expansion, self.num_cls)
        self.feature_dim = 512 * block.expansion

    def setup_critera(self):
        self.criterion_cls_hard = nn.CrossEntropyLoss()
        self.criterion_cls_soft = self._make_distill_criterion(alpha=self.alpha, T=self.T)

    @staticmethod
    def _make_distill_criterion(alpha=0.5, T=4.0):
        print('** alpha is {} and temperature is {} **\n'.format(alpha, T))
        def criterion(outputs, labels, targets):
            # Soft cross entropy
            _p = F.log_softmax(outputs / T, dim=1)
            _q = F.softmax(targets / T, dim=1)

            # _soft_loss = -torch.mean(torch.sum(_q * _p, dim=1))
            _soft_loss = nn.KLDivLoss()(_p, _q)

            # Soft hard combination
            _soft_loss = _soft_loss * T * T
            _hard_loss = F.cross_entropy(outputs, labels)
            loss = alpha * _soft_loss + (1. - alpha) * _hard_loss
            return loss
        return criterion

    def load(self, init_path, feat_only=False):

        if 'http' in init_path:
            init_weights = load_state_dict_from_url(init_path, progress=True)
        else:
            init_weights = torch.load(init_path)

        if feat_only:
            init_weights = OrderedDict({k.replace('feature.', ''): init_weights[k] for k in init_weights})
            self.feature.load_state_dict(init_weights, strict=False)
            load_keys = set(init_weights.keys())
            self_keys = set(self.feature.state_dict().keys())
        else:
            self.load_state_dict(init_weights, strict=False)
            load_keys = set(init_weights.keys())
            self_keys = set(self.state_dict().keys())

        missing_keys = self_keys - load_keys
        unused_keys = load_keys - self_keys
        print("missing keys: {}".format(sorted(list(missing_keys))))
        print("unused_keys: {}".format(sorted(list(unused_keys))))

    def save(self, out_path):
        torch.save(self.best_weights, out_path)

    def update_best(self):
        self.best_weights = copy.deepcopy(self.state_dict())

