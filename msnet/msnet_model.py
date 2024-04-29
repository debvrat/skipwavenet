
""" Full assembly of the parts to form the complete network """

#import torch.nn.functional as F
#import torch.nn as nn
#import torch

from .msnet_parts import *


class msNet(nn.Module):
    def __init__(self, n_channels=3, n_classes=1):
        super(msNet, self).__init__()

        self.n_channels = n_channels
        self.n_classes = n_classes


        self.conv1 = DoubleConv(n_channels, 64)
        self.conv2 = Down(64, 128) #1/2
        self.conv3 = Down3(128, 256) #1/4
        self.conv4 = Down3(256, 512) #1/8
        self.conv5 = Down3(512, 512) #1/16


        self.dsn1 = nn.Conv2d(64, 1, 1)
        self.dsn2 = nn.Conv2d(128, 1, 1)
        self.dsn3 = nn.Conv2d(256, 1, 1)
        self.dsn4 = nn.Conv2d(512, 1, 1)
        self.dsn5 = nn.Conv2d(512, 1, 1)
        self.fuse = nn.Conv2d(5, 1, 1)
        
        self.wav1 = waveT(1,'dmey') ## added by @dv
        
        self.fusew_2 = nn.Conv2d(4, 1, 1)
        self.fusew_3 = nn.Conv2d(4, 1, 1)
        self.fusew_4 = nn.Conv2d(4, 1, 1)
        #self.fusew_5 = nn.Conv2d(2, 1, 1)
        self.fusew_5 = nn.Conv2d(4, 1, 1) # @dv: added
        # Upsample


        # self.up2 = Up2(stride=2)
        # self.up3 = Up2(stride=4)
        # self.up4 = Up2(stride=8)
        # self.up5 = Up2(stride=16)


    def forward(self, x,w):
        #h = x.size(2)
        #w = x.size(3)
        img_H, img_W = x.shape[2], x.shape[3]
        conv1 = self.conv1(x) #64
        conv2 = self.conv2(conv1)#1/2 #128
        conv3 = self.conv3(conv2)#1/4 #256
        conv4 = self.conv4(conv3)#1/8 #512
        conv5 = self.conv5(conv4)#1/16 #512



        if w is not None:

            so1 = self.dsn1(conv1)
            w1 = self.wav1(so1)
            so22 = self.dsn2(conv2)#1/2
            fcat_2=torch.cat((so22, w1['cH1'], w1['cV1'], w1['cD1']), dim=1)
            so2 =self.fusew_2(fcat_2)
            w2 = self.wav1(so2)
            
            so33 = self.dsn3(conv3)#1/4
            fcat_3=torch.cat((so33, w2['cH1'],w2['cV1'], w2['cD1']), dim=1)
            so3 =self.fusew_3(fcat_3)
            w3 = self.wav1(so3)
            
            so44 = self.dsn4(conv4)#1/8
            fcat_4=torch.cat((so44, w3['cH1'],w3['cV1'], w3['cD1']), dim=1)
            so4 =self.fusew_4(fcat_4)
            w4 = self.wav1(so4)

            so5 = self.dsn5(conv5)#1/16
           # fcat_5=torch.cat((so55, w['cA4']), dim=1)
            #so5 =self.fusew_5(fcat_5)
            
            fcat_5=torch.cat((so5, w4['cH1'],w4['cV1'], w4['cD1']), dim=1) # @dv: added
            so5 =self.fusew_5(fcat_5) # @dv: added
            
        else:
            # side output
            so1 = self.dsn1(conv1) # size: [1,1,H,W]
            so2 = self.dsn2(conv2)
            so3 = self.dsn3(conv3)
            so4 = self.dsn4(conv4)
            so5 = self.dsn5(conv5)

            #so1 = self.up1(conv1) # size: [1,1,H,W]
            # so2 = self.up2(img_H, img_W , so2)
            # so3 = self.up3(img_H, img_W , so3)
            # so4 = self.up4(img_H, img_W , so4)
            # so5 = self.up5(img_H, img_W , so5)

        if torch.cuda.is_available():
            weight_deconv2 =  make_bilinear_weights(4, 1).cuda()
            weight_deconv3 =  make_bilinear_weights(8, 1).cuda()
            weight_deconv4 =  make_bilinear_weights(16, 1).cuda()
            weight_deconv5 =  make_bilinear_weights(32, 1).cuda()
        else:
            weight_deconv2 =  make_bilinear_weights(4, 1)
            weight_deconv3 =  make_bilinear_weights(8, 1)
            weight_deconv4 =  make_bilinear_weights(16, 1)
            weight_deconv5 =  make_bilinear_weights(32, 1)

        upsample2 = F.conv_transpose2d(so2, weight_deconv2, stride=2)
        upsample3 = F.conv_transpose2d(so3, weight_deconv3, stride=4)
        upsample4 = F.conv_transpose2d(so4, weight_deconv4, stride=8)
        upsample5 = F.conv_transpose2d(so5, weight_deconv5, stride=16)

        so1 = crop(so1, img_H, img_W)
        so2 = crop(upsample2, img_H, img_W)
        so3 = crop(upsample3, img_H, img_W)
        so4 = crop(upsample4, img_H, img_W)
        so5 = crop(upsample5, img_H, img_W)


        fusecat = torch.cat((so1, so2, so3, so4, so5), dim=1)
        fuse = self.fuse(fusecat)
        results = [so1, so2, so3, so4, so5, fuse]
        results = [torch.sigmoid(r) for r in results]

        return results
