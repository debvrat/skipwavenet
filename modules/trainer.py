import os
import numpy as np
from PIL import Image
import time
import torch

import logging
from tqdm import tqdm
from torch.utils.tensorboard import SummaryWriter
from torch.utils.data import DataLoader, random_split

from torch.optim import lr_scheduler
import torchvision
import cv2


from os.path import join, split, isdir, isfile, splitext

from msnet import msNet
from modules.models_utils import convert_vgg , weights_init, vgg_pretrain, resume
from modules.functions import  cross_entropy_loss # sigmoid_cross_entropy_loss
from modules.utils import Averagvalue #, save_checkpoint



#from tensorboardX import SummaryWriter



class Network(object):
    def __init__(self, args, model):
        super(Network, self).__init__()


        self.model = model
        self.model.apply(weights_init)


        if args.pretrained_path is not None:
            self.model.apply(weights_init)
            vgg_pretrain(model=model, pretrained_path=args.pretrained_path)

        if args.resume_path is not None:
            resume(model=model, resume_path=args.resume_path)

        if torch.cuda.is_available():
            self.model.cuda()


class Trainer(object):
    def __init__(self, args, net, train_loader, val_loader=None):
        super(Trainer, self).__init__()

        self.model=net.model

        self.train_loader = train_loader
        self.val_loader = val_loader

        self.n_train=len(train_loader)
        if val_loader is not None:
            self.n_val=len(val_loader)
        else:
            self.n_val=0

        self.n_dataset= self.n_train+self.n_val
        self.global_step = 0

        self.batch_size=args.batch_size

        #losses
        self.train_loss = []
        self.train_loss_detail = []

        self.val_loss = []
        self.val_loss_detail = []

        self.max_epoch=args.max_epoch


        self.use_cuda=torch.cuda.is_available()

        # training args
        self.itersize=args.itersize

        #tune lr
        tuned_lrs=tune_lrs(self.model,args.lr, args.weight_decay)

        self.optimizer = torch.optim.SGD(tuned_lrs, lr=args.lr, momentum=args.momentum, weight_decay=args.weight_decay)


        self.scheduler = lr_scheduler.StepLR(self.optimizer, step_size=args.stepsize, gamma=args.gamma)

        self.writer = SummaryWriter(args.log_dir)

    def train(self, save_dir, epoch):

        ## initilization
        losses = Averagvalue()
        epoch_loss = []

        val_losses = Averagvalue()
        epoch_val_loss = []


        counter = 0
        with tqdm(total=self.n_train, desc=f'Epoch {epoch + 1}/{self.max_epoch}', unit='img') as pbar:
            for batch in self.train_loader:

                image, label, image_name, w= batch['image'] , batch['mask'] , batch['id'][0], batch.get('w',None)

                if torch.cuda.is_available():
                    image, label = image.cuda(), label.cuda()
                    if w is not None: ## added by @dv
                        for key in w: 
                            w[key]=w[key].cuda()

                ## forward
                outputs = self.model(image, w)
                ## loss


                if self.use_cuda:
                    loss = torch.zeros(1).cuda()
                else:
                    loss = torch.zeros(1)


                for o in outputs:
                    loss = loss+cross_entropy_loss(o, label)
                #loss=self.loss_w(loss_r)

                counter += 1
                loss = loss / self.itersize
                loss.backward()

                # SDG step
                if counter == self.itersize:
                    self.optimizer.step()
                    self.optimizer.zero_grad()
                    counter = 0
                    self.global_step += 1

                # measure accuracy and record loss
                losses.update(loss.item(), image.size(0))
                epoch_loss.append(loss.item())

                self.writer.add_scalar('Loss/train', loss.item(), self.global_step)
                pbar.set_postfix(**{'loss (batch)': loss.item()})
                pbar.update(image.shape[0])



                if self.global_step % (self.n_dataset // (10 * self.batch_size)) == 0:
                    ## logging ## 4 lines below commented by @dv
                    # for tag, value in self.model.named_parameters():
                    #     tag = tag.replace('.', '/')
                    #     self.writer.add_histogram('weights/' + tag, value.data.cpu().numpy(), self.global_step)
                    #     self.writer.add_histogram('grads/' + tag, value.grad.data.cpu().numpy(), self.global_step)


                    self.writer.add_images('images', image, self.global_step)
                    self.writer.add_images('masks/true', label, self.global_step)
                    self.writer.add_images('masks/pred', outputs[-1] > 0.5, self.global_step)



                    outputs.append(label)
                    outputs.append(image)

                    dev_checkpoint(save_dir=join(save_dir, f'training-epoch-{epoch+1}-record'),
                               i=self.global_step, epoch=epoch, image_name=image_name, outputs= outputs)


        self.save_state(epoch, save_path=join(save_dir, f'checkpoint_epoch{epoch+1}.pth'))
        self.writer.add_scalar('Loss_avg', losses.avg, epoch+1)
        self.train_loss.append(losses.avg)
        self.train_loss_detail += epoch_loss
        if val_losses.count>0:
            self.writer.add_scalar('Val_Loss_avg', val_losses.avg, epoch+1)
        self.writer.add_scalar('learning_rate', self.optimizer.param_groups[0]['lr'], self.global_step)
        #adjust learnig rate
        self.scheduler.step()

    def dev(self,dev_loader, save_dir, epoch):
        print("Running test ========= >")
        self.model.eval()
        if not isdir(save_dir):
            os.makedirs(save_dir)
        for batch in dev_loader:

            image,image_id, w= batch['image'] ,  batch['id'][0] , batch['w']


            if self.use_cuda:
                image = image.cuda()
                for key in w:
                    w[key]=w[key].cuda()
            _, _, H, W = image.shape

            with torch.no_grad():
               outputs = self.model(image, w)

            outputs.append(1-outputs[-1])
            outputs.append(batch['mask'])
            dev_checkpoint(save_dir, 0, epoch, image_id, outputs)

            # result=tensor2image(results[-1])
            # result_b=tensor2image(1-results[-1])

            # cv2.imwrite(join(save_dir, f"{image_id}.png".replace('image','fuse')), result)
            # cv2.imwrite(join(save_dir, f"{image_id}.jpg".replace('image','fuse')), result_b)


    def save_state(self, epoch, save_path='checkpoint.pth'):
        torch.save({
                    'epoch': epoch,
                    'state_dict': self.model.state_dict(),
                    'optimizer': self.optimizer.state_dict()
                    }, save_path)



##========================== adjusting lrs

def tune_lrs(model, lr, weight_decay):

    bias_params= [param for name,param in list(model.named_parameters()) if name.find('bias')!=-1]
    weight_params= [param for name,param in list(model.named_parameters()) if name.find('weight')!=-1]

    conv1_4_weights , conv1_4_bias  = weight_params[0:10]  , bias_params[0:10]
    conv5_weights   , conv5_bias    = weight_params[10:13] , bias_params[10:13]
    d1_5_weights    , d1_5_bias     = weight_params[13:18] , bias_params[13:18]
    fuse_weights , fuse_bias =weight_params[-1] , bias_params[-1]

    tuned_lrs=[
            {'params': conv1_4_weights, 'lr': lr*1    , 'weight_decay': weight_decay},
            {'params': conv1_4_bias,    'lr': lr*2    , 'weight_decay': 0.},
            {'params': conv5_weights,   'lr': lr*100  , 'weight_decay': weight_decay},
            {'params': conv5_bias,      'lr': lr*200  , 'weight_decay': 0.},
            {'params': d1_5_weights,    'lr': lr*0.01 , 'weight_decay': weight_decay},
            {'params': d1_5_bias,       'lr': lr*0.02 , 'weight_decay': 0.},
            {'params': fuse_weights,    'lr': lr*0.001, 'weight_decay': weight_decay},
            {'params': fuse_bias ,      'lr': lr*0.002, 'weight_decay': 0.},
            ]
    return  tuned_lrs


##=========================== train_split func

def dev_checkpoint(save_dir, i, epoch, image_name, outputs):
    # display and logging
    if not isdir(save_dir):
        os.makedirs(save_dir)
        ## use torchvision
        # _, _, H, W = outputs[0].shape
        # all_results = torch.zeros((len(outputs), 1, H, W))
        # for j in range(len(outputs)):
        #     all_results[j, 0, :, :] = outputs[j][0, 0, :, :]
        # # res=torch.squeeze(all_results.detach()).cpu().permute(1, 2, 0).numpy()
        # torchvision.utils.save_image(all_results, join(save_dir, "iter-{}-{}.png".format(i, image_name))) #image_name[5:11]

        ## use cv2
        
    outs=[]
    for o in outputs:
        outs.append(tensor2image(o))
    if len(outs[-1].shape)==3:
        outs[-1]=outs[-1][0,:,:] #if RGB, show one layer only

    out=cv2.hconcat(outs) # if gray
    cv2.imwrite(join(save_dir, f"global_step-{i}-{image_name}.jpg"), out)

def tensor2image(image):
            result = torch.squeeze(image.detach()).cpu().numpy()
            result = (result * 255).astype(np.uint8, copy=False)
            #(torch.squeeze(o.detach()).cpu().numpy()*255).astype(np.uint8, copy=False)
            return result
