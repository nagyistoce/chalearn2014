"""
Video classifier using a 3D deep convolutional neural network

Data: ChaLearn 2014 gesture challenge: gesture recognition

"""
scaler = 127.5
scaler_traj = 10
try_doc = "21lbls aug stdnorm 64x64"
print try_doc

# own imports
from data_aug import start_load, load_normal
from convnet3d import ConvLayer, NormLayer, PoolLayer, LogRegr, HiddenLayer, \
                    DropoutLayer, relu

# various imports
from cPickle import dump, load
from glob import glob
from time import time, localtime
from gzip import GzipFile
import os
import shutil
import string
from scipy import ndimage

# numpy imports
from numpy import ones, array, prod, zeros, empty, inf, float32, random

# theano imports
from theano import function, config, shared
from theano.tensor.nnet import conv2d
from theano.tensor import TensorType
import theano.tensor as T
# from theano.sandbox.cuda import CudaNdarrayType, CudaNdarray


# constants
floatX = config.floatX
rng = random.RandomState(1337) # this will make sure results are always the same

def normalize(input,newmin=-1,newmax=1):
    mini = T.min(input)
    maxi = T.max(input)
    return (input-mini)*(newmax-newmin)/(maxi-mini)+newmin

"""                                                                                                                                                  
 8 8888   b.             8   8 8888  8888888 8888888888 
 8 8888   888o.          8   8 8888        8 8888       
 8 8888   Y88888o.       8   8 8888        8 8888       
 8 8888   .`Y888888o.    8   8 8888        8 8888       
 8 8888   8o. `Y888888o. 8   8 8888        8 8888       
 8 8888   8`Y8o. `Y88888o8   8 8888        8 8888       
 8 8888   8   `Y8o. `Y8888   8 8888        8 8888       
 8 8888   8      `Y8o. `Y8   8 8888        8 8888       
 8 8888   8         `Y8o.`   8 8888        8 8888       
 8 8888   8            `Yo   8 8888        8 8888                     
"""

# pc = "laptop"
pc = "reslab"
# pc = "kot"
if pc=="laptop":
    src = "/home/lio/mp/chalearn2014/32x128x128_train/batch" # dir of preprocessed data
    res_dir_ = '/home/lio/Dropbox/MP/chalearn2014/results' # dir to print and store results
elif pc=="reslab":
    src = "/home/lpigou/batch" # dir of preprocessed data
    res_dir_ = '/mnt/storage/usr/lpigou/chalearn2014/results' # dir to print and store results
elif pc=="kot":
    src = "/home/lio/mp/chalearn2014/40x90x90_train" # dir of preprocessed data
    res_dir_ = '/home/lio/Dropbox/MP/chalearn2014/results' # dir to print and store results

batch_size = 100
in_shape = (batch_size,2,2,32,64,64) # (batchsize, maps, frames, w, h) input video shapes 
# in_shape = (batch_size,2,3,32,128,128) # (batchsize, maps, frames, w, h) input video shapes 
traj_shape = (batch_size,3,32) # (batchsize, input shape of the trajectory

# hyper parameters
# ------------------------------------------------------------------------------

# use techniques/methods
class use:
    drop = True # dropout
    depth = True # use depth map as input
    aug = True # data augmentation
    load = False # load params.p file
    traj = False # trajectory
    trajconv = False # convolutions on trajectory
    valid2 = False
    fast_conv = True
    norm_div = False

    norm = True # normalization layer
    mom = True # momentum

# learning rate
class lr:
    init = 3e-3 # lr initial value
    decay = .95 # lr := lr*decay
    decay_big = .1
    decay_each_epoch = True
    decay_if_plateau = True

class batch:
    mini = 20 # number of samples before updating params
    micro = 4 if pc=="reslab" else 1 # number of samples that fits in memory

# regularization
class reg:
    L1_traj = .0 # degree/amount of regularization
    L2_traj = .0 # 1: only L1, 0: only L2
    L1_vid = .0 # degree/amount of regularization
    L2_vid = .0 # 1: only L1, 0: only L2

# momentum
class mom:
    momentum = .9 # momentum value
    nag = True # use nesterov momentum

# training
class tr:
    n_epochs = 1000 # number of epochs to train
    patience = 1 # number of unimproved epochs before decaying learning rate

# dropout
class drop:
    p_traj_val = float32(0.5) # dropout on traj
    p_vid_val = float32(0.5) # dropout on vid
    p_hidden_val = float32(0.5) # dropout on hidden units

class trajconv:
    append = False # append convolutions result to original traject
    filter_size = 5
    layers = 3 # number of convolution layers
    res_shape = traj_shape[-1]-layers*(filter_size-1)

class net:
    shared_stages = [] # stages where weights are shared
    shared_convnets = [] # convnets that share weights ith beighbouring convnet
    n_convnets = 2 # number of convolutional networks in the architecture
    maps = [2,16,32,64] # feature maps in each convolutional network
    # maps = [2,16,32,32] # feature maps in each convolutional network
    kernels = [(1,7,7), (1,8,8), (1,6,6)] # convolution kernel shapes
    pools = [(2,2,2), (2,2,2), (2,2,2)] # pool/subsampling shapes
    W_scale = [[0.01,0.01],[0.01,0.01],[0.01,0.01],0.01,0.01]
    b_scale = 0.1
    stride = [1,1,1]
    hidden_traj = 200 # hidden units in MLP
    hidden_vid = 300 # hidden units in MLP
    norm_method = "lcn" # normalisation method: lcn = local contrast normalisation
    pool_method = "max" # maxpool
    fusion = "early" # early or late fusion
    hidden = hidden_traj+hidden_vid if fusion=="late" else 500 # hidden units in MLP
    n_class = 21

activation = relu # tanh, sigmoid, relu, softplus

#  helper functions
# ------------------------------------------------------------------------------

def _shared(val, borrow=True):
    return shared(array(val, dtype=floatX), borrow=borrow)

def _avg(_list): return list(array(_list).mean(axis=0))

lt = localtime()
res_dir = res_dir_+"/try/"+str(lt.tm_year)+"."+str(lt.tm_mon).zfill(2)+"." \
            +str(lt.tm_mday).zfill(2)+"."+str(lt.tm_hour).zfill(2)+"."\
            +str(lt.tm_min).zfill(2)+"."+str(lt.tm_sec).zfill(2)+"."\
            +" "+try_doc
os.makedirs(res_dir)
def write(_s): 
    with open(res_dir+"/output.txt","a") as f: f.write(_s+"\n")
    print _s

def ndtensor(n): return TensorType(floatX, (False,)*n) # n-dimensional tensor

#  global variables/constants
# ------------------------------------------------------------------------------

params = [] # all neural network parameters
layers = [] # all architecture layers
mini_updates = []
micro_updates = []
last_upd = []
update = []
t_W, t_b = [],[] # trajectory filters
first_report = True # report timing if first report
moved = False
load_params_pos = 0 # position in parameter list when loading parameters
video_shapes = [in_shape[-3:]]
n_stages = len(net.kernels)
traj_size = prod(traj_shape[1:]) # 20 frames, 2 hands, 3 coords

# shared variables
learning_rate = shared(float32(lr.init))
if use.mom: 
    momentum = shared(float32(mom.momentum))
    drop.p_vid = shared(float32(drop.p_vid_val) )
    drop.p_hidden = shared(float32(drop.p_hidden_val))
    drop.p_traj = shared(float32(drop.p_traj_val))

# symbolic variables
x = ndtensor(len(in_shape))(name = 'x') # video input
# x = T.TensorVariable(CudaNdarrayType([False] * len(in_shape))) # video input
t = ndtensor(len(traj_shape))(name='t') # trajectory input
y = T.ivector(name = 'y') # labels
idx_mini = T.lscalar(name="idx_mini") # minibatch index
idx_micro = T.lscalar(name="idx_micro") # microbatch index
    
# print parameters
# ------------------------------------------------------------------------------

for c in (use, lr, batch, net, reg, drop, mom, tr):
    write(c.__name__+":")
    _s = c.__dict__
    del _s['__module__'], _s['__doc__']
    for key in _s.keys(): 
        val = str(_s[key])
        if val.startswith("<static"): val = str(_s[key].__func__.__name__)
        if val.startswith("<Cuda"): continue
        if val.startswith("<Tensor"): continue
        write("  "+key+": "+val)
print "activation:", activation.__name__

"""                                                               
8 888888888o.            .8.    8888888 8888888888   .8.          
8 8888    `^888.        .888.         8 8888        .888.         
8 8888        `88.     :88888.        8 8888       :88888.        
8 8888         `88    . `88888.       8 8888      . `88888.       
8 8888          88   .8. `88888.      8 8888     .8. `88888.      
8 8888          88  .8`8. `88888.     8 8888    .8`8. `88888.     
8 8888         ,88 .8' `8. `88888.    8 8888   .8' `8. `88888.    
8 8888        ,88'.8'   `8. `88888.   8 8888  .8'   `8. `88888.   
8 8888    ,o88P' .888888888. `88888.  8 8888 .888888888. `88888.  
8 888888888P'   .8'       `8. `88888. 8 8888.8'       `8. `88888.    
"""

x_ = _shared(empty(in_shape))
# x_ = shared(CudaNdarray(empty(in_shape, dtype=floatX)), borrow=True)
t_ = _shared(empty(traj_shape))
y_ = _shared(empty((batch_size,)))
y_int32 = T.cast(y_,'int32')


# set up file location and distribution
_files = glob(src+'/batch_100_*.zip')+glob(src+'/valid/batch_100_*.zip')
# _files = glob(src+'_train/batch_100_*.p')
# _files.sort()
# _files = _files[:10]
rng.shuffle(_files)
class files:
    data_files = _files
    n_train = int(len(data_files) *  .8)
    n_valid = int(len(data_files) *  .2)
    train = data_files[:n_train]
    valid = data_files[n_train:n_train+n_valid]
    if use.valid2: valid2 = data_files[n_train+n_valid:]
    # valid2 = glob(src+'_valid/batch_100_*.p')

# data augmentation
jobs,queue = start_load(files.train,augm=use.aug,start=True)

# print data sizes
if use.valid2: files.n_test = len(files.valid2)
else: files.n_test = 0
write('data: total: %i train: %i valid: %i test: %i' % \
    ((files.n_test+files.n_train+files.n_valid)*batch_size, 
        files.n_train*batch_size, 
        files.n_valid*batch_size, 
        files.n_test*batch_size))

first_report2 = True
def load_data(path, trans): 
    global rng, x_,t_,y_,first_report2
    """ load data into shared variables """

    # if trans and use.aug:
    #     transform(path) # que up the path for augmentation
    #     vid, traj, lbl = load_aug(path)
    # else:
    #     vid, traj, lbl = load_normal(path)

    # file = GzipFile(path, 'rb')
    # vid, skel, lbl = load(file)
    # file.close()
    # traj,ori,pheight = skel

    # print path
    # import cv2
    # for img in vid[0,0,0]:
    #      cv2.imshow("Video", img)
    #      cv2.waitKey(0)
    # for img in vid[0,0,1]:
    #      cv2.imshow("Video", img)
    #      cv2.waitKey(0)

    # new_vid = empty(in_shape,dtype="uint8")

    # vid_ = vid[:,0,:2,:,::2,::2]
    # vid_ = vid[:,0,:2]
    # zm = 1.*90./128.
    # vid_ = ndimage.zoom(vid_,(1,1,1,zm,zm),order=0)
    # new_vid[:,0] = vid_
    # new_vid[:,1] = vid[:,1,:2]

    # print "loading..."
    start_time = time()
    if not trans: 
        start_load(files.valid,jobs,False)
    vid, skel, lbl = queue.get()[0]
    traj,ori,pheight = skel

    print "get in",str(time()-start_time)[:3]+"s",

    # shuffle data
    ind = rng.permutation(batch_size)
    vid, traj, lbl = vid[ind].astype(floatX), traj[ind].astype(floatX),lbl[ind].astype(floatX)

    # vid = vid/(255./(scaler*2.))-scaler
    # traj = traj/(255./(scaler_traj*2.))-scaler_traj
    # traj = traj/(255./5.)

    lbl -= 1

    if first_report2:
        print "data range:",vid.min(),vid.max()
        print "traj range:",traj.min(),traj.max()
        print "lbl range:",lbl.min(),lbl.max()
        first_report2 = False

    # set value
    x_.set_value(vid, borrow=True)
    t_.set_value(traj, borrow=True)
    y_.set_value(lbl, borrow=True)

def load_params():
    global load_params_pos
    par = load(open("params.p", "rb"))
    W = par[load_params_pos]
    b = par[load_params_pos+1]
    load_params_pos +=2
    return W,b

"""                                                                 
8 888888888o   8 8888      88  8 8888 8 8888         8 888888888o.      
8 8888    `88. 8 8888      88  8 8888 8 8888         8 8888    `^888.   
8 8888     `88 8 8888      88  8 8888 8 8888         8 8888        `88. 
8 8888     ,88 8 8888      88  8 8888 8 8888         8 8888         `88 
8 8888.   ,88' 8 8888      88  8 8888 8 8888         8 8888          88 
8 8888888888   8 8888      88  8 8888 8 8888         8 8888          88 
8 8888    `88. 8 8888      88  8 8888 8 8888         8 8888         ,88 
8 8888      88 ` 8888     ,8P  8 8888 8 8888         8 8888        ,88' 
8 8888    ,88'   8888   ,d8P   8 8888 8 8888         8 8888    ,o88P'   
8 888888888P      `Y88888P'    8 8888 8 888888888888 8 888888888P'                          
""" 
print "\n%s\n\tbuilding\n%s"%(('-'*50,)*2)

# ConvNet
# ------------------------------------------------------------------------------

# calculate resulting video shapes for all stages
conv_shapes = []
for i in xrange(n_stages):
    k,p,v = array(net.kernels[i]), array(net.pools[i]), array(video_shapes[i])
    conv_s = tuple(v-k+1)
    conv_shapes.append(conv_s)
    video_shapes.append(tuple((v-k+1)/p))
    print "stage", i
    print "  conv",video_shapes[i],"->",conv_s
    print "  pool",conv_s,"->",video_shapes[i+1],"x",net.maps[i+1]

# number of inputs for MLP = (# maps last stage)*(# convnets)*(resulting video shape) + trajectory size
n_in_MLP = net.maps[-1]*net.n_convnets*prod(video_shapes[-1]) 
if use.traj: n_in_MLP += traj_size
print 'MLP:', n_in_MLP, "->", net.hidden, "->", net.n_class, ""

def conv_args(stage, i):
    """ ConvLayer arguments, i: stage index """
    args = {
        'batch_size':batch.micro, 
        'activation':activation, 
        'rng':rng,
        'n_in_maps':net.maps[stage],
        'n_out_maps':net.maps[stage+1], 
        'kernel_shape':net.kernels[stage], 
        'video_shape':video_shapes[stage],
        "fast_conv":use.fast_conv,
        "layer_name":"Conv"+str(stage)+str(i),
        "W_scale":net.W_scale[stage][i],
        "b_scale":net.b_scale,
        "stride":net.stride[stage]
    }
    if stage in net.shared_stages and i in net.shared_convnets:
        print "sharing weights!"
        args["W"], args["b"] = layers[-1].params # shared weights
    elif use.load:
        args["W"], args["b"] = load_params(stage, i) # load stored parameters
    return args

if use.depth:
    if net.n_convnets==1: out = [x[:,0]]
    elif net.n_convnets==2: out = [x[:,0], x[:,1]] # 2 nets: left and right
    else: out = [x[:,0,0:1], x[:,0,1:2], x[:,1,0:1], x[:,1,1:2]] # 4 nets
else: 
    if net.n_convnets==1: out = [x[:,0,0:1]]
    else: out = [x[:,0,0:1], x[:,1,0:1]] # 2 nets without depth: left and right

# out = [x[:,0,1:2], x[:,1,1:2]]

def var_norm(_x,imgs=True,axis=[-3,-2,-1]):
    if imgs:
        return (_x-T.mean(_x,axis=axis,keepdims=True))/T.maximum(1e-4,T.std(_x,axis=axis,keepdims=True))
    return (_x-T.mean(_x))/T.maximum(1e-4,T.std(_x))
def std_norm(_x,axis=[-3,-2,-1]):
    return _x/T.maximum(1e-4,T.std(_x,axis=axis,keepdims=True))

# for i in xrange(len(out)): out[i] = var_norm(out[i])
# build 3D ConvNet
for stage in xrange(n_stages):
    for i in xrange(len(out)): # for each convnet of the stage
        # normalization
        if use.norm and stage==0: 
            gray_norm = NormLayer(out[i][:,0:1], method="lcn",
                use_divisor=False).output
            gray_norm = std_norm(gray_norm)
            depth_norm = var_norm(out[i][:,1:])
            out[i]  = T.concatenate([gray_norm,depth_norm],axis=1)
        elif use.norm:
            out[i] = NormLayer(out[i], method="lcn",use_divisor=False).output
            out[i] = std_norm(out[i])
        # convolutions  
        layers.append(ConvLayer(out[i], **conv_args(stage, i)))
        out[i] = layers[-1].output
        # print stage, i
        # #ccn
        # if use.norm: out[i] = NormLayer(out[i],method="ccn",
        #     shape=(batch.micro,net.maps[stage+1])+conv_shapes[stage]).output
        # pooling, subsamping
        out[i] = PoolLayer(out[i], net.pools[stage], method=net.pool_method).output

        # out[i] = normalize(out[i])

# flatten all convnets outputs
# for i in xrange(len(out)): out[i] = var_norm(out[i])
out = [out[i].flatten(2) for i in range(len(out))]
vid_ = T.concatenate(out, axis=1)
# vid_ = var_norm(vid_,axis=1)

# traject convolution
# ------------------------------------------------------------------------------

if use.trajconv:
    t_conv = t.reshape((batch.micro*prod(traj_shape[1:-1]), 1,1,traj_shape[-1]))
    t_filt_sh = (1, 1, 1, trajconv.filter_size)
    n_out = traj_shape[-1]
    for i in xrange(trajconv.layers):
        t_W.append(_shared(rng.normal(loc=0, scale=0.01, size=t_filt_sh)))
        t_conv = conv2d(t_conv, 
            filters=t_W[-1], 
            filter_shape=t_filt_sh, 
            border_mode='valid')
        n_out -= trajconv.filter_size - 1
        t_b.append(_shared(ones((n_out,), dtype=floatX)*0.1))
        t_conv = t_conv + t_b[-1].dimshuffle('x',0)
        t_conv = activation(t_conv)

    conv_length = prod(traj_shape[1:-1])*trajconv.res_shape
    t_conv = t_conv.reshape((batch.micro, conv_length))
    if trajconv.append: 
        traj_ = T.concatenate([t.flatten(2), t_conv.flatten(2)], axis=1)
    else: 
        traj_ = t_conv.flatten(2)
        n_in_MLP -= traj_size
    n_in_MLP += conv_length

elif use.traj: traj_ = var_norm(t.flatten(2),axis=1)

insp = T.stack(T.min(vid_), T.mean(vid_), T.max(vid_), T.std(vid_))#, T.min(traj_), T.mean(traj_), T.max(traj_), T.std(traj_))
# dropout
if use.drop: 
    if use.traj: traj_ = DropoutLayer(traj_, rng=rng, p=drop.p_traj).output
    vid_ = DropoutLayer(vid_, rng=rng, p=drop.p_vid).output

# MLP
# ------------------------------------------------------------------------------

# fusion
if net.fusion == "early":
    if use.traj:
        out = T.concatenate([vid_, traj_], axis=1)
    else: out = vid_
    # hidden layer
    layers.append(HiddenLayer(out, n_in=n_in_MLP, n_out=net.hidden, rng=rng, 
        W_scale=net.W_scale[-2], b_scale=net.b_scale, activation=activation))
    out = layers[-1].output
else: # late fusion
    n_in_MLP -= net.maps[-1]*net.n_convnets*prod(video_shapes[-1])
    layers.append(HiddenLayer(traj_, n_in=n_in_MLP, n_out=net.hidden_traj, rng=rng, 
        W_scale=net.W_scale[-2], b_scale=net.b_scale, activation=activation))
    n_in_MLP = net.maps[-1]*net.n_convnets*prod(video_shapes[-1])
    layers.append(HiddenLayer(vid_, n_in=n_in_MLP, n_out=net.hidden_vid, rng=rng, 
        W_scale=net.W_scale[-2], b_scale=net.b_scale, activation=activation))
    out = T.concatenate([layers[-1].output, layers[-2].output], axis=1)

# out = normalize(out)

if use.drop: out = DropoutLayer(out, rng=rng, p=drop.p_hidden).output

# softmax layer
layers.append(LogRegr(out, rng=rng, activation=activation, n_in=net.hidden, 
    W_scale=net.W_scale[-1], b_scale=net.b_scale, n_out=net.n_class))


#-------------------LATE LATE FUSION---------------------------------------------
# vid_out = layers[-1].p_y_given_x

# n_hidden = 200

# traj_ = t.flatten(2)
# layers.append(HiddenLayer(traj_, n_in=traj_size, n_out=n_hidden, rng=rng, 
#         W_scale=0.01, b_scale=net.b_scale, activation=activation))
# traj_out = layers[-1].output

# traj_out = DropoutLayer(traj_out, rng=rng, p=drop.p_hidden).output

# layers.append(LogRegr(traj_out, rng=rng, activation=activation, n_in=n_hidden, 
#     W_scale=0.01, b_scale=net.b_scale, n_out=20))

# traj_out = layers[-1].p_y_given_x

# out = T.concatenate([vid_out,traj_out], axis=1)

# layers.append(LogRegr(out, rng=rng, activation=activation, n_in=2*20, 
#     W_scale=net.W_scale[-1], b_scale=net.b_scale, n_out=20))
#-------------------LATE LATE FUSION---------------------------------------------


"""
layers[-1] : softmax layer
layers[-2] : hidden layer (video if late fusion)
layers[-3] : hidden layer (trajectory, only if late fusion)
"""

# cost function
cost = layers[-1].negative_log_likelihood(y)

if reg.L1_vid > 0 or reg.L2_vid > 0:
    # L1 and L2 regularization
    L1 = T.abs_(layers[-2].W).sum() + T.abs_(layers[-1].W).sum()
    L2 = (layers[-2].W ** 2).sum() + (layers[-1].W ** 2).sum()

    cost += reg.L1_vid*L1 + reg.L2_vid*L2 

if net.fusion == "late":
    L1_traj = T.abs_(layers[-3].W).sum()
    L2_traj = (layers[-3].W ** 2).sum()
    cost += reg.L1_traj*L1_traj + reg.L2_traj*L2_traj

# function computing the number of errors
errors = layers[-1].errors(y)

# gradient descent
# ------------------------------------------------------------------------------

# parameter list
for layer in layers: params.extend(layer.params)
if use.trajconv: 
    params.extend(t_W)
    params.extend(t_b)
# gradient list
gparams = T.grad(cost, params)
assert len(gparams)==len(params)

def get_update(i): return update[i]/(batch.mini/batch.micro)

for i, (param, gparam) in enumerate(zip(params, gparams)):

    # shape of the parameters
    shape = param.get_value(borrow=True).shape
    # init updates := zeros
    update.append(_shared(zeros(shape, dtype=floatX)))
    # micro_updates: sum of lr*grad
    micro_updates.append((update[i], update[i] + learning_rate*gparam))
    # re-init updates to zeros
    mini_updates.append((update[i], zeros(shape, dtype=floatX)))

    if use.mom:
        last_upd.append(_shared(zeros(shape, dtype=floatX)))
        v = momentum * last_upd[i] - get_update(i)
        mini_updates.append((last_upd[i], v))
        if mom.nag: # nesterov momentum
            mini_updates.append((param, param + momentum*v - get_update(i)))
        else:
            mini_updates.append((param, param + v))
    else:    
        mini_updates.append((param, param - get_update(i)))

"""                                          .         .                                                                
    ,o888888o.        ,o888888o.           ,8.       ,8.          8 888888888o     
   8888     `88.   . 8888     `88.        ,888.     ,888.         8 8888    `88.   
,8 8888       `8. ,8 8888       `8b      .`8888.   .`8888.        8 8888     `88   
88 8888           88 8888        `8b    ,8.`8888. ,8.`8888.       8 8888     ,88   
88 8888           88 8888         88   ,8'8.`8888,8^8.`8888.      8 8888.   ,88'   
88 8888           88 8888         88  ,8' `8.`8888' `8.`8888.     8 888888888P'    
88 8888           88 8888        ,8P ,8'   `8.`88'   `8.`8888.    8 8888           
`8 8888       .8' `8 8888       ,8P ,8'     `8.`'     `8.`8888.   8 8888           
   8888     ,88'   ` 8888     ,88' ,8'       `8        `8.`8888.  8 8888           
    `8888888P'        `8888888P'  ,8'         `         `8.`8888. 8 8888                
"""
print "\n%s\n\tcompiling\n%s"%(('-'*50,)*2)

# compile functions
# ------------------------------------------------------------------------------

def get_batch(_data): 
    pos_mini = idx_mini*batch.mini
    idx1 = pos_mini + idx_micro*batch.micro
    idx2 = pos_mini + (idx_micro+1)*batch.micro
    return _data[idx1:idx2]

def givens(dataset_):
    return {x: get_batch(dataset_[0]),
            t: get_batch(dataset_[1]),
            y: get_batch(dataset_[2])}

print 'compiling apply_updates'
apply_updates = function([], 
    updates=mini_updates, 
    on_unused_input='ignore')

print 'compiling train_model'
# train_model = function([idx_mini, idx_micro], [cost, errors, debug], 
# train_model = function([idx_mini, idx_micro], [cost, errors], 
train_model = function([idx_mini, idx_micro], [cost, errors, insp], 
    updates=micro_updates, 
    givens=givens((x_,t_,y_int32)), 
    on_unused_input='ignore')

print 'compiling test_model'
test_model = function([idx_mini, idx_micro], [cost, errors], 
    givens=givens((x_,t_,y_int32)),
    on_unused_input='ignore')

"""                                                                               
8888888 8888888888 8 888888888o.            .8.           8 888     8 8888 b.             8 
      8 8888       8 8888    `88.          .888.          8 888     8 8888 888o.          8 
      8 8888       8 8888     `88         :88888.                   8 8888 Y88888o.       8 
      8 8888       8 8888     ,88        . `88888.                  8 8888 .`Y888888o.    8 
      8 8888       8 8888.   ,88'       .8. `88888.       8 888     8 8888 8o. `Y888888o. 8 
      8 8888       8 888888888P'       .8`8. `88888.      8 888     8 8888 8`Y8o. `Y88888o8 
      8 8888       8 8888`8b          .8' `8. `88888.     8 888     8 8888 8   `Y8o. `Y8888 
      8 8888       8 8888 `8b.       .8'   `8. `88888.    8 888     8 8888 8      `Y8o. `Y8 
      8 8888       8 8888   `8b.    .888888888. `88888.   8 888     8 8888 8         `Y8o.` 
      8 8888       8 8888     `88. .8'       `8. `88888.  8 888     8 8888 8            `Yo 
"""
print "\n%s\n\ttraining\n%s"%(('-'*50,)*2)

time_start = 0

# reporting
# ------------------------------------------------------------------------------

def timing_report(train_time):
    global first_report
    r = "\nTraining: %.2fms / sample\n"% (1000.*train_time/batch_size,) 
    first_report = False
    write(r)

def training_report(train_ce):
    return "%5.3f %5.2f" % (train_ce[0], train_ce[1]*100.)

def print_params(): 
    for param in params[::2]:
        p = param.get_value(borrow=True)
        print param.name+" %.4f %.4f %.4f %.4f %i"%(p.min(),p.mean(),p.max(),p.std(),len(p[p==0]))

def epoch_report(epoch, train_ce, valid_ce, valid2_ce=None):
    result_string = """ 
    epoch %i: %.2f m since start, LR %.2e
    training_cost: %.3f, training_error: %.3f
    validation_cost: %.3f, validation_error: %.3f""" % \
    (epoch, (time() - time_start) / 60., learning_rate.get_value(borrow=True), 
        train_ce[0], train_ce[1]*100., valid_ce[0], valid_ce[1]*100.)

    if valid2_ce:
        result_string += "\n\tvalidation2_cost: %.3f, validation2_error: %.3f"%\
        (valid2_ce[0], valid2_ce[1]*100.)

    write(result_string)


def save_results(train_ce, valid_ce, valid2_ce=None):
    global res_dir
    dst = res_dir.split("/")
    if dst[-1].find("%")>=0:
        d = dst[-1].split("%")
        d[0] = str(valid_ce[-1][1]*100)[:4]
        dst[-1] = string.join(d,"%")
    else:
        dst[-1] = str(valid_ce[-1][1]*100)[:4]+"% "+dst[-1]
    dst = string.join(dst,"/") 
    shutil.move(res_dir, dst)
    res_dir = dst
    file = GzipFile(res_dir+"/params.zip", 'wb')
    dump(params, file, -1)
    file.close()
    if valid2_ce: ce = (train_ce, valid_ce, valid2_ce)
    else: ce = (train_ce, valid_ce)
    with open(res_dir+"/cost_error.txt","wb") as f: f.write(str(ce)+"\n")
    dump(ce, open(res_dir+"/cost_error.p", "wb"), -1)

def move_results():
    global moved, res_dir
    dst = res_dir.split("/")
    dst = dst[:-2]  + [dst[-1]] 
    dst = string.join(dst,"/") 
    shutil.move(res_dir, dst)
    res_dir = dst
    moved = True
    shutil.copy(__file__, res_dir)

#  training, validation, test
# ------------------------------------------------------------------------------  
insp_ = None
def _mini_batch(model, mini_batch, is_train):
    global insp_
    ce = []
    for i in xrange(batch.mini/batch.micro):
        if not is_train:
            ce.append(model(mini_batch, i))
        else:
            c_,e_,insp_ = model(mini_batch, i) 
            ce.append([c_,e_])
    if is_train: apply_updates()
    return _avg(ce)

def _batch(model, is_train=True):
    ce = []
    for i in xrange(batch_size/batch.mini): ce.append(_mini_batch(model, i, is_train))
    return _avg(ce)

def test(files_):
    global jobs
    if use.drop: # dont use dropout when testing
        drop.p_traj.set_value(float32(0.)) 
        drop.p_vid.set_value(float32(0.)) 
        drop.p_hidden.set_value(float32(0.)) 
    ce = []
    first_test_file = True
    for file in files_:
        if first_test_file:
            augm = False
            first_test_file = False
        else: augm = True
        load_data(file,  augm)
        ce.append(_batch(test_model, False))
    if use.drop: # reset dropout
        drop.p_traj.set_value(drop.p_traj_val) 
        drop.p_vid.set_value(drop.p_vid_val) 
        drop.p_hidden.set_value(drop.p_hidden_val)
    start_load(files.train,augm=use.aug)
    return _avg(ce)

# main loop
# ------------------------------------------------------------------------------

lr_decay_epoch = 0
n_lr_decays = 0
best_valid = inf
train_ce, valid_ce, valid2_ce = [], [], []
flag=True

# valid_ce.append(test(files.valid))

for epoch in xrange(tr.n_epochs):
    ce = []
    print_params()

    

    for i,train_file in enumerate(files.train):
        if epoch==0 and i==1: time_start = time()
        #load
        load_data(train_file, True)
        # train
        ce.append(_batch(train_model))

        #print 
        print "\t\t| "+training_report(ce[-1])
        #timing report
        if i==1 and first_report: timing_report(time()-time_start)

    # End of Epoch
    #-------------------------------
    print insp_
    train_ce.append(_avg(ce))
    # if flag and train_ce[-1][1] < 0.9: 
        # learning_rate.set_value(float32(0.001))
        # flag = False

    # validate
    valid_ce.append(test(files.valid))
    if use.valid2:
        valid2_ce.append(test(files.valid2))

    # save best params
    if train_ce[-1][1] < 0.25 and valid_ce[-1][1] < best_valid:
        best_valid = valid_ce[-1][1]
        if use.valid2: save_results(train_ce, valid_ce, valid_ce)
        else: save_results(train_ce, valid_ce)
        if not moved: move_results()

    # report
    if use.valid2: epoch_report(epoch, train_ce[-1], valid_ce[-1], valid2_ce[-1])
    else: epoch_report(epoch, train_ce[-1], valid_ce[-1])
    # make_plot(train_ce, valid_ce)

    if lr.decay_each_epoch:
        learning_rate.set_value(float32(learning_rate.get_value(borrow=True)*lr.decay))
    # elif lr.decay_if_plateau:
    #     if epoch - lr_decay_epoch > tr.patience \
    #         and valid_ce[-1-tr.patience][1] <= valid_ce[-1][1]:

    #         write("Learning rate decay: validation error stopped improving")
    #         lr_decay_epoch = epoch
    #         n_lr_decays +=1
    #         learning_rate.set_value(float32(learning_rate.get_value(borrow=True)*lr.decay_big))
    # if epoch == 0: 
        # learning_rate.set_value(float32(3e-4))
    # else:
        # learning_rate.set_value(float32(learning_rate.get_value(borrow=True)*lr.decay))

    rng.shuffle(files.train)

if use.aug: 
    for job in jobs: job.join()
"""
import matplotlib.pyplot as plt
def make_plot(train_ce, valid_ce): 
    tr = array(train_ce)[:,1]*100.
    va = array(valid_ce)[:,1]*100.
    x = range(1,tr.shape[0]+1)

    plt.plot(x, tr, 'rs--', label='train')
    plt.plot(x, va, 'bo-', label='valid')
    plt.ylabel('Error (%)')
    plt.xlabel('Epoch')
    plt.xlim([0,tr.shape[0]+1])
    plt.ylim([0,95])
    plt.legend()
    plt.savefig(res_dir+'/plot.pdf', bbox_inches='tight')
    plt.close()
    plt.clf()
    plt.cla()
"""