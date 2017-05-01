from functools import reduce as _reduce
import operator as _operator

import mxnet.ndarray as _nd
import mxnet.contrib.autograd as _autograd

import minpy.nn.init as _init
import minpy.nn.optim as _optim
import minpy.nn.model as _model


_module_counter = {}


def _module_prefix(module_name):
    index = _module_counter.setdefault(module_name, 0)
    _module_counter[module_name] += 1
    prefix = '%s%d' % (module_name, index)
    return prefix


def _is_array(array):
    return isinstance(array, _nd.NDArray)


from collections import Iterable as _Iterable
def _is_iterable(instance):
    return isinstance(instance, _Iterable)


class Module(object):
    # pylint: disable=too-few-public-methods
    """ Base class of module.
    :param dict initializer: Initializer.
    """

    _module_name = None
    def __init__(self, name):
        super(Module, self).__init__()
        if self._module_name is None: raise NotImplementedError()
        self._name = _module_prefix(self._module_name) if name is None else name

    @property
    def name(self):
        return self._name

    def forward(self, *args, **kwargs):
        raise NotImplementedError()

    def __call__(self, *args, **kwargs):
        raise NotImplementedError()
        
    def __setitem__(self, _):
        raise NotImplementedError()

    def __setitem__(self, _):
        raise NotImplementedError()

    def __repr__(self):
        return str(self)

    def __str__(self):
        return self._name

    # TODO support scalar operation
    def __add__(self, other):
        return Add(self, other)

    def __sub__(self, other):
        return Sub(self, other)

    def __mul__(self, other):
        return Mul(self, other)

    def _affiliate_to(self, model):
        '''
            responsible for
                (1). refering to model.params and model.aux_params
                (2). update model._update_configs
                (3). if isinstance(self, Container): call _affiliate_to of contained modules
        '''
        raise NotImplementedError()


class Container(Module):
    # TODO provide an interface for indexing contained modules
    def __init__(self, name):
        super(Container, self).__init__(name)

    def __call__(self, *args, **kwargs):
        return self.forward(*args, **kwargs)

    def param_shapes(self, input_shape):
        return {}

    def aux_param_shapes(self, input_shape):
        return {}


class Sequential(Container):
    _module_name = 'sequential'
    def __init__(self, *args, **kwargs):
        """ Sequential network.

        :param Module args: all layers of the feedforward networks in sequential order.
        """

        name = kwargs.get('name', None)
        super(Sequential, self).__init__(name)

        assert all(isinstance(arg, Module) for arg in args), TypeError()

        self._modules = list(args)

        self.append = self._modules.append
        self.insert = self._modules.insert
        self.pop = self._modules.pop
        self.reverse = self._modules.reverse
        self.__iter__ = self._modules.__iter__

    def __repr__(self):
        return str(self)

    def __str__(self):
        return str(self._modules)

    def forward(self, *args):
        # It is recommended that all forward functions, especially those in Sequential, only receive positional arguments.
        to_tuple = lambda results : results if isinstance(results, tuple) else (results,)
        forward_module = lambda args, module : to_tuple(module(*args))
        result = _reduce(forward_module, self._modules, args)
        if len(result) == 1: result, = result
        return result

    def training(self):
        for module in self._modules:
            module.training()

    def inference(self):
        for module in self._modules:
            module.inference()

    def _affiliate_to(self, model):
        for module in self._modules:
            module._affiliate_to(model)

class Parallel(Container):
    def __init__(self, name=None):
        super(Parallel, self).__init__(name)


class Binary(Parallel):
    # TODO training/inference
    def __init__(self, left, right, operator, name):
        super(Binary, self).__init__(name)
        self._left, self._right = left, right
        self._operator = operator

    def __str__(self):
        # TODO prettify
        return '%s %s %s' % (str(self._left), self._module_name, str(self._right))

    def forward(self, X):
        left = self._left(X)
        right = self._right(X)
        return self._operator(left, right)

    def training(self):
        self._left.training()
        self._right.training()

    def inference(self):
        self._left.inference()
        self._right.inference()

    def _affiliate_to(self, model):
        for module in (self._left, self._right):
            module._affiliate_to(model)
   

class Add(Binary):
    _module_name = 'add'
    def __init__(self, left, right, name=None):
        super(Add, self).__init__(left, right, _operator.add, name)


class Sub(Binary):
    _module_name = 'sub'
    def __init__(self, left, right, name=None):
        super(Sub, self).__init__(left, right, _operator.sub, name)


class Mul(Binary):
    _module_name = 'mul'
    def __init__(self, left, right, name=None):
        super(Mul, self).__init__(left, right, _operator.mul, name)


# TODO div etc.

def _register_configs(configs, to_register):
    ''' param dict configs: pair param_name(str) : param_configs(dict)
        param to_register: pair param_name(str) : param_configs(dict) or attr(str) : attr_value(object)
    '''

    _configs = {}

    # register global attributes
    for identifier, attr_value in to_register.items():
        assert isinstance(identifier, str), KeyError() # global attribute must be str (e.g. 'learning_rate')
        if identifier in configs:
            # identifier is a param name and attr_value is a dict containing configs specific to this parameter
            # memorize and skip it
            _configs[identifier] = attr_value
        else: 
            # a global attribute, modify this attribute for all parameters
            for config in configs.values():
                # TODO the modification might be ineligible
                config[identifier] = attr_value

    # register parameter-specific attributes
    for identifier, attr_value in _configs.items():
        # parameter-specific attribute values overwrite global attribute values
        configs[identifier].update(attr_value)


class Layer(Module):
    def __init__(self, params=None, aux_params=None, name=None):
        '''
            Currently, a layer must be bound to a model.
        '''

        super(Layer, self).__init__(name)

        if params is None: params = tuple()
        self._module_param_names = params                     # local param names
        self._param_names = self._assign_param_names(*params) # global param names (identifiable in model)
        for param, param_name in zip(params, self._param_names):
            setattr(self, param, param_name)
        # TODO Is it necessary to bind one layer to multiple models?

        if aux_params is None: aux_params = tuple()
        self._module_aux_param_names = aux_params                     # local aux param names
        self._aux_param_names = self._assign_param_names(*aux_params) # global aux param names (identifiable in model)
        for aux_param, aux_param_name in zip(aux_params, self._aux_param_names):
            setattr(self, '_%s' % aux_param, aux_param_name)

        self._model = None

        # default init configs
        default_init_configs = \
            {name : self._get_default_init_config(name) for name in self._module_param_names}
        default_init_configs.update(
            {name : self._get_default_init_config(name) for name in self._module_aux_param_names}
        )

        self._init_configs = {name : {} for name in self._param_names}
        self._init_configs.update({name : {} for name in self._aux_param_names})

        self._register_init_configs(default_init_configs)

        # default update configs
        # user must specify update configs explicitly
        default_update_configs = {'update_rule' : 'unspecified'}

        self._update_configs = {name : {} for name in self._param_names}

        self._register_update_configs(default_update_configs)

        self._mode = 'training' # training/inference

        self._to_initialize = True

    def __call__(self, *args, **kwargs):
        assert bool(self._model), 'A layer must be bound to a model.'
        # initialize only if self is bound to a model
        if self._to_initialize:
            array_args = tuple(filter(_is_array, args))
            array_kwargs = {key : value for key, value in kwargs.items() if is_array(value)}

            get_shape = lambda array : array.shape
            arg_shapes = tuple(map(get_shape, array_args))
            kwarg_shapes = dict(zip(array_kwargs.keys(), tuple(map(get_shape, array_kwargs.values()))))

            get_context = lambda array : array.context
            contexts = set(map(get_context, args + tuple(kwargs.values())))
            context = contexts.pop()
            assert not contexts, ''

            # initialize params
            param_shapes = self.param_shapes(*arg_shapes, **kwarg_shapes)
            self._init_params(param_shapes, context)

            # initialize aux params
            aux_param_shapes = self.aux_param_shapes(*arg_shapes, **kwarg_shapes)
            self._init_aux_params(aux_param_shapes, context)

            self._to_initialize = False

        return self.forward(*args, **kwargs)

    def _assign_param_names(self, *params):
        return tuple('%s_%s' % (self._name, param) for param in params)
    
    def _affiliate_to(self, model):
        model._update_configs.update(self._update_configs)
        self._model = model

    @staticmethod
    def _get_default_init_config(param_name):
        if 'weight' in param_name:
            return {'init_rule' : 'xavier'}
        elif 'bias' in param_name:
            return {'init_rule' : 'constant', 'value' : 0}
        elif 'beta' in param_name:
            return {'init_rule' : 'constant', 'value' : 0}
        elif 'gamma' in param_name:
            return {'init_rule' : 'constant', 'value' : 1}
        elif 'moving_mean' in param_name:
            return {'init_rule' : 'constant', 'value' : 0}
        elif 'moving_var' in param_name:
            return {'init_rule' : 'constant', 'value' : 1}
        else:
            return {'init_rule' : 'constant', 'value' : 0}
 
    def _init_params(self, param_shapes, context):
        # param_shapes: dict
        for name, shape in param_shapes.items():
            # init only if param is absent (to support pre-loading params)
            if name not in self._model.params:
                init_config = self._init_configs[name]
                self._model.params[name] = \
                    getattr(_init, init_config['init_rule'])(shape, init_config) \
                    .as_in_context(context)
            if self._model._attach_all:
                self._model.attach(name, self._model.params[name])

        # register update_configs in model
        
    def _init_aux_params(self, aux_param_shapes, context):
        # aux_param_shapes: dict
        for name, shape in aux_param_shapes.items():
            # init only if aux param is absent (to support pre-loading aux params)
            if name not in self._model.aux_params:
                init_config = self._init_configs[name]
                self._model.aux_params[name] = \
                    getattr(_init, init_config['init_rule'])(shape, init_config) \
                    .as_in_context(context)

    def _get_param(self, param_name):
        # should not be called prior to calling __call__ for the first time
        return self._model.params[param_name]

    def _get_params(self, *param_names):
        # should not be called prior to calling __call__ for the first time
        return tuple(self._model.params[param_name] for param_name in param_names)

    def _get_aux_param(self, aux_param_name):
        # should not be called prior to calling __call__ for the first time
        return self._model.aux_params[aux_param_name]

    def _get_aux_params(self, *aux_param_names):
        # should not be called prior to calling __call__ for the first time
        return tuple(self._model.aux_params[aux_param_name] for aux_param_name in aux_param_names)

    def _parse_param_configs(self, configs):
        '''
            parsed configs might contain:
                1. pair param_name(str) : configs(dict), which are parameter-specific configs
                2. pair attr_name(str) : attr_value(object), which are global configs
        '''
        if configs is None: return {}

        _configs = {key : value for key, value in configs.items()}

        for identifier in _configs:
            if isinstance(identifier, tuple): # configs applied to a group of parameters
                config = _configs.pop(identifier)
                # expand group configs to parameter-specific configs
                for param_name in identifier: _configs[param_name] = config

        # convert local (module) name to global name
        for module_param_name, param_name in \
            zip(self._module_param_names, self._param_names):
            if module_param_name in _configs:
                _configs[param_name] = _configs.pop(module_param_name)

        # convert local (module) name to global name
        for module_aux_param_name, aux_param_name in \
            zip(self._module_aux_param_names, self._aux_param_names):
            if module_aux_param_name in _configs:
                _configs[aux_param_name] = _configs.pop(module_aux_param_name)

        return _configs

    def _register_init_configs(self, init_configs):
        # Parent Layer class provides global default init_configs.
        # A child (probably customized) layer may replace global default init_configs.

        init_configs = self._parse_param_configs(init_configs)
        _register_configs(self._init_configs, init_configs)

    def _register_update_configs(self, update_configs):
        # Parent Layer class provides global default update_configs.
        # A child (probably customized) layer may replace global default update_configs.

        update_configs = self._parse_param_configs(update_configs)
        _register_configs(self._update_configs, update_configs)

    def forward(self, *args, **kwargs):
        raise NotImplementedError()

    def training(self):
        self._mode = 'training'

    def inference(self):
        self._mode = 'inference'

    @property
    def param_dict(self):
        return dict(zip(self._param_names, self._get_params(*self._param_names)))

    @property
    def aux_param_dict(self):
        return dict(zip(self._aux_param_names, self._get_aux_params(*self._aux_param_names)))
   
    def param_shapes(self, *args, **kwargs):
        # customized layer must specify the shapes of ALL params
        return {}

    def aux_param_shapes(self, *args, **kwargs):
        # customized layer must specify the shapes of ALL aux params
        return {}


class Model(_model.ModelBase):
    # TODO detach/resume (parameter, layer)
    # TODO check duplicated layers
    def __init__(self, loss=None, attach_all=True):
        """
        param loss: LinearRegression, LogisticRegression, MAERegression, Softmax, SVM
        """

        super(Model, self).__init__()

        if isinstance(loss, str):
            loss = getattr(_nd, loss + 'Output')
        if callable(loss):
            self.loss = loss
            '''
            def _(*args, **kwargs):
                with _autograd.TrainingStateScope(kwargs.pop('is_train', False)):
                    return loss(*args, **kwargs)
            self.loss = _
            '''

        self._update_configs = {}

        self._modules = set()      # references to all registered modules
        self._module_names = set() # names of all registered modules

        self.grad_dict = {}

        self._attach_all = attach_all

    def __setattr__(self, attr, attr_value):
        '''
            All modules containing states modified by model (e.g. parameters,
            training/inference mode) should be registered as an attribute of model.
        '''
        # TODO training/inference

        if isinstance(attr_value, Model):
            # caution: user must ensure that there is no duplicated parameter name
            _register_model(self, attr_value)
        elif isinstance(attr_value, Module):
            self._register_module(attr_value)
        elif _is_iterable(attr_value):
            self._register_iterable(attr_value)

        object.__setattr__(self, attr, attr_value)

    def _register_model(self, model):
        model.attach = lambda _, name, array : self.attach(name, array)
        model.detach = lambda _, name : self.detach(name)

    def _register_module(self, module):
        # check duplication
        assert module not in self._modules
        assert module.name not in self._module_names

        self._modules.add(module)
        self._module_names.add(module.name)
        
        module._affiliate_to(self)

    def _register_iterable(self, iterable):
        for element in iterable:
            if isinstance(element, Module):
                self._register_module(element)
            elif isinstance(element, str): continue
            elif _is_iterable(element):
                self._register_iterable(element)

    # disable several inherited attributes and methods
    # TODO cannot set attribute
    '''
    @property
    def param_configs(self):
        raise NotImplementedError()

    @property
    def aux_param_configs(self):
        raise NotImplementedError()
    '''
        
    def add_param(*args, **kwargs):
        raise NotImplementedError()
 
    def add_params(*args, **kwargs):
        raise NotImplementedError()
        
    def add_aux_param(*args, **kwargs):
        raise NotImplementedError()
 
    # requires implementation
    def forward(self):
        raise NotImplementedError()
    
    def forward_batch(self):
        # TODO eliminate?
        raise NotImplementedError()

    @staticmethod
    def decorator(forward):
        def wrapped(self, *args, **kwargs):
            is_train = kwargs.get('is_train', False)
            if is_train: self.training()
            else: self.inference()
            with _autograd.TrainingStateScope(is_train):
                return forward(self, *args, **kwargs)
        return wrapped

    def attach(self, name, array):
        if name not in self.grad_dict:
            self.grad_dict[name] = _nd.zeros(array.shape)
            _autograd.mark_variables((array,), (self.grad_dict[name],))
        return self

    def detach(self, name):
        raise NotImplementedError()

    def grad(self):
        raise NotImplementedError()

    def grad_and_loss(self, data, labels):
        raise NotImplementedError()

    # TODO load/save (inherited method)

    def training(self):
        # training mode
        for module in self._modules:
            module.training()
  
    def inference(self):
        # inference mode
        for module in self._modules:
            module.inference()

class _ConfigParser(object):
    class _ParamRef(object):
        def __init__(self, param_configs):
            object.__setattr__(self, '_param_configs', param_configs)

        def __getattr__(self, attr):
            return self._param_configs[attr]

        def __setattr__(self, attr, attr_value):
            self._param_configs[attr] = attr_value

    def __init__(self, configs):
        object.__setattr__(self, '_configs', configs)
    
    def __getattr__(self, attr):
        attr_values = set(config[attr] for config in self._configs.values() if attr in config)
        assert len(attr_values) == 1, 'Inconsistent or non-existent attribute.'
        return attr_values.pop()

    def __setattr__(self, attr, attr_value):
        # modify configurations globally
        for config in self._configs.values():
            config[attr] = attr_value

    def __getitem__(self, param_name):
        return _ConfigParser._ParamRef(self._configs[param_name])

    def __setitem__(self, param_name, configs):
        # modify configurations corresponding to a parameter completely
        assert isinstance(configs, dict)
        self._configs[param_name] = dict(configs)

    def keys(self):
        return self._configs.keys()

    def values(self):
        return self._configs.values()

    def items(self):
        return self._configs.items()


class Updater(_ConfigParser):
    def __init__(self, model, **kwargs):
        # duplicate so that there could be multiple updaters for one model
        configs = dict(model._update_configs)
        super(Updater, self).__init__(configs)
        object.__setattr__(self, '_model', model)

        # only accept attributes applying to all parameters in constructor
        # those attributes are local to self
        for attr, attr_value in kwargs.items():
            setattr(self, attr, attr_value)
    
    def __call__(self, grad_dict):
        """ Only update parameters corresponding to gradients contained in grad_dict.
            User could update parameters selectively by manipulating grad_dict.
        """
        for param_name, grad in grad_dict.items():
            param = self._model.params[param_name]
            update_rule = self._configs[param_name].pop('update_rule')
            update_config = self._configs[param_name]
            self._model.params[param_name], _update_config = \
                getattr(_optim, update_rule)(param, grad, update_config)
            self._configs[param_name]['update_rule'] = update_rule
            update_config.update(_update_config)
