#!/usr/bin/env python3
from __future__ import absolute_import, division, print_function, unicode_literals
import argparse
import os
import warnings
import time
import random
import accimage
import numpy as np
import sys
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt#; plt.rcdefaults()

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torchvision
import torchvision.transforms as transforms
from torch.autograd import Variable
from utils import *

import modelarchs
import lqnet


def test(val_loader, model, epoch, args):
    batch_time = AverageMeter('Time', ':6.3f')
    losses = AverageMeter('Loss', ':.4e')
    top1 = AverageMeter('Acc@1', ':6.2f')
    top5 = AverageMeter('Acc@5', ':6.2f')
    progress = ProgressMeter(
        len(val_loader),
        [batch_time, losses, top1, top5],
        prefix='Test: ')

    # switch to evaluate mode
    model.eval()


    confidence_record = torch.tensor([],dtype=torch.float32).cuda()#np.array([])
    pred_record = torch.tensor([], dtype=torch.int).cuda()#np.array([]).astype(int)
    target_record = torch.tensor([], dtype=torch.int).cuda()#np.array([]).astype(int)

    with torch.no_grad():
        end = time.time()
        # apply quantized value to testing stage
        #if args.lq:
            #LQ.apply_quantval()

        for i, (images, target) in enumerate(val_loader):
            #if args.gpu is not None:
            #    images = images.cuda(args.gpu, non_blocking=True)
            #target = target.cuda(args.gpu, non_blocking=True)
            images, target = Variable(images.cuda()), Variable(target.cuda())
            target_record = torch.cat((target_record, target.type(torch.int).reshape(-1)), 0)

            # compute output
            output = model(images)
            loss = criterion(output, target)
            #print("output size",output.size())
            #print("target size",target.size())

            # confidence and pred of the output
            _, pred = output.topk(1, 1, True, True)
            pred_record = torch.cat((pred_record, pred.type(torch.int).reshape(-1)), 0)
            confidence = F.softmax(output.data, dim=1).max(1)[0]
            confidence_record = torch.cat((confidence_record, confidence.type(torch.float).reshape(-1)), 0)

            # measure accuracy and record loss
            acc1, acc5 = accuracy(output, target, topk=(1, 5))
            losses.update(loss.item(), images.size(0))
            top1.update(acc1[0], images.size(0))
            top5.update(acc5[0], images.size(0))

            # measure elapsed time
            batch_time.update(time.time() - end)
            end = time.time()

            if i % 10 == 0:
                progress.display(i)

        #restore the floating point value to W
        #if args.lq:
            #LQ.restoreW()

        # TODO: this should also be done with the ProgressMeter
        print(' * Acc@1 {top1.avg:.3f} Acc@5 {top5.avg:.3f}'
              .format(top1=top1, top5=top5))


    return top1.avg


def train(train_loader,optimizer, model, epoch, args):
    batch_time = AverageMeter('Time', ':6.3f')
    data_time = AverageMeter('Data', ':6.3f')
    losses = AverageMeter('Loss', ':.4e')
    top1 = AverageMeter('Acc@1', ':6.2f')
    top5 = AverageMeter('Acc@5', ':6.2f')
    progress = ProgressMeter(
        len(train_loader),
        [batch_time, data_time, losses, top1, top5],
        prefix="Epoch: [{}]".format(epoch))
    end = time.time()

    model.train()
    for i, (images, target) in enumerate(train_loader):
        # measure data loading time
        data_time.update(time.time() - end)

        #if args.gpu is not None:
            #images = images.cuda(args.gpu, non_blocking=True)
        #target = target.cuda(args.gpu, non_blocking=True)
        images, target = Variable(images.cuda()), Variable(target.cuda())

        # apply quantized value to W
        #if args.lq:
            #LQ.apply_quantval()

        # compute output
        output = model(images)
        loss = criterion(output, target)

        # measure accuracy and record loss
        acc1, acc5 = accuracy(output, target, topk=(1, 5))
        losses.update(loss.item(), images.size(0))
        top1.update(acc1[0], images.size(0))
        top5.update(acc5[0], images.size(0))

        if args.lq:
            LQ.update()

        # compute gradient and do SGD step
        optimizer.zero_grad()
        loss.backward()

        #print('apply quantized')
        #LQ.print_weights()

        # use gradients of Bi to update Wi
        if args.lq:
            LQ.restoreW()
        #print('before step')
        #LQ.print_weights()
        optimizer.step()

        #print('after step')
        #LQ.print_weights()

        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()
        if i % 100 == 0:
            progress.display(i,optimizer)

    print('Finished Training')

    if args.lq:
        #if epoch % 10 == 9:
        LQ.print_info()
        if epoch == args.epochs -1:
            LQ.apply_quantval()

    return

def accuracy(output, target, topk=(1,)):
    """Computes the accuracy over the k top predictions for the specified values of k"""
    with torch.no_grad():
        maxk = max(topk)
        batch_size = target.size(0)

        _, pred = output.topk(maxk, 1, True, True)
        pred = pred.t()
        correct = pred.eq(target.view(1, -1).expand_as(pred))

        res = []
        for k in topk:
            correct_k = correct[:k].view(-1).float().sum(0, keepdim=True)
            res.append(correct_k.mul_(100.0 / batch_size))
        return res


if __name__=='__main__':
    imagenet_datapath= '/data2/jiecaoyu/imagenet/imgs/'
    parser = argparse.ArgumentParser(description='PyTorch MNIST ResNet Example')
    parser.add_argument('--no_cuda', default=False, 
            help = 'do not use cuda',action='store_true')
    parser.add_argument('--epochs', type=int, default=450, metavar='N',
            help='number of epochs to train (default: 450)')
    parser.add_argument('--start-epoch', default=0, type=int, metavar='N',
                    help='manual epoch number (useful on restarts)')
    parser.add_argument('--lr_epochs', type=int, default=100, metavar='N',
            help='number of epochs to change lr (default: 100)')
    parser.add_argument('--pretrained', default=None, nargs='+',
            help='pretrained model ( for mixtest \
            the first pretrained model is the big one \
            and the sencond is the small net)')
    parser.add_argument('--seed', default=None, type=int,
                    help='seed for initializing training. ')
    parser.add_argument('--lr', '--learning-rate', default=0.1, type=float,
                    metavar='LR', help='initial learning rate', dest='lr')
    parser.add_argument('--momentum', default=0.9, type=float, metavar='M',
                    help='momentum')
    parser.add_argument('--wd', '--weight-decay', default=1e-4, type=float,
                    metavar='W', help='weight decay (default: 1e-4)',
                    dest='weight_decay')
    parser.add_argument('-e', '--evaluate', dest='evaluate', action='store_true',
                    default=False, help='evaluate model on validation set')
    parser.add_argument('--gpu', default=None, type=int,
                    help='GPU id to use.')
    parser.add_argument('--arch', action='store', default='resnet20',
                        help='the CIFAR10 network structure: resnet20 | resnet18')
    parser.add_argument('--dataset', action='store', default='cifar10',
            help='pretrained model: cifar10 | imagenet')
    parser.add_argument('--lq', default=False, 
            help = 'use lq-net quantization or not',action='store_true')
    parser.add_argument('--bits', default = [2,2,2,2,2,2,2,2,2], type = int,
                    nargs = '*', help = ' num of bits for each layer')

    args = parser.parse_args()
    args.cuda = not args.no_cuda and torch.cuda.is_available()
    print(args)

    if args.seed is not None:
        random.seed(args.seed)
        torch.manual_seed(args.seed)
        cudnn.deterministic = True
        warnings.warn('You have chosen to seed training. '
                      'This will turn on the CUDNN deterministic setting, '
                      'which can slow down your training considerably! '
                      'You may see unexpected behavior when restarting '
                      'from checkpoints.')

    if args.dataset == 'cifar10':
        # load cifa-10
        nclass = 10
        normalize = transforms.Normalize(
                mean=[0.491, 0.482, 0.447], std=[0.247, 0.243, 0.262])
        trainset = torchvision.datasets.CIFAR10(root='./data', train=True,
                                            download=True, transform=
                                            transforms.Compose([
                                                transforms.RandomCrop(32,padding=4),
                                                transforms.RandomHorizontalFlip(),
                                                transforms.ToTensor(),
                                                normalize,
                                                ]))
        trainloader = torch.utils.data.DataLoader(trainset, batch_size=64,
                                              shuffle=True, num_workers=16)

        testset = torchvision.datasets.CIFAR10(root='./data', train=False,
                                           download=True, transform=
                                           transforms.Compose([
                                               transforms.RandomCrop(32),
                                               transforms.ToTensor(),
                                               normalize,
                                               ]))
        testloader = torch.utils.data.DataLoader(testset, batch_size=64,
                                             shuffle=False, num_workers=16)


    if args.dataset == 'imagenet':
        nclass=100 
        traindir = os.path.join(imagenet_datapath,'train')
        testdir = os.path.join(imagenet_datapath,'val')
        torchvision.set_image_backend('accimage')

        normalize = transforms.Normalize(
                mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        trainset = torchvision.datasets.ImageFolder(root=traindir,transform=
                                            transforms.Compose([
                                                #transforms.Resize(256),
                                                #transforms.CenterCrop(args.crop),
                                                #transforms.RandomCrop(args.crop),
                                                transforms.RandomResizedCrop(224),
                                                transforms.RandomHorizontalFlip(),
                                                transforms.ToTensor(),
                                                normalize,
                                                ]))
        trainloader = torch.utils.data.DataLoader(trainset, batch_size=256,
                                              shuffle=True, num_workers=16)

        testset = torchvision.datasets.ImageFolder(root=testdir,transform=
                                           transforms.Compose([
                                               transforms.Resize(256),
                                               transforms.CenterCrop(224),
                                               transforms.ToTensor(),
                                               normalize,
                                               ]))
        testloader = torch.utils.data.DataLoader(testset, batch_size=256,
                                             shuffle=False, num_workers=16)


    if args.arch == 'resnet20':
        model = modelarchs.resnet20(nclass=nclass,ds=args.ds)
        

    elif args.arch == 'resnet18':
        #pretrained = False if args.pretrained is not None else True
        pretrained = True
        model = torchvision.models.resnet18(pretrained = pretrained)
        bestacc = 0

    elif args.arch == 'all_cnn_c':
        model = modelarchs.all_cnn_c()

    criterion = nn.CrossEntropyLoss().cuda()
    optimizer = optim.SGD(model.parameters(), 
                lr=args.lr, momentum=args.momentum, weight_decay= args.weight_decay)

    if not args.pretrained:
        bestacc = 0
    elif args.mix == 0:
        pretrained_model = torch.load(args.pretrained[0])
        best_acc = pretrained_model['acc']
        args.start_epoch = pretrained_model['epoch']
        load_state(model, pretrained_model['state_dict'])
        optimizer.load_state_dict(pretrained_model['optimizer'])

    if args.cuda:
        model.cuda()
        model = nn.DataParallel(model, 
                    device_ids=range(torch.cuda.device_count()))
        #model = nn.DataParallel(model, device_ids=args.gpu)

    print(model)

    if args.lq:
        LQ = lqnet.learned_quant(model, b = args.bits)



    ''' evaluate model accuracy and loss only '''
    if args.evaluate:
        test(testloader, model, args.start_epoch, args)
        exit()

    ''' train model '''

    for epoch in range(args.start_epoch,args.epochs):
        running_loss = 0.0
        adjust_learning_rate(optimizer, epoch, args)
        train(trainloader,optimizer, model, epoch, args)
        acc = test(testloader, model, epoch, args)
        if (acc > bestacc):
            bestacc = acc
            save_state(model,acc,epoch,args, optimizer, True)
        else:
            save_state(model,bestacc,epoch,args,optimizer, False)
        print('best acc so far:{:4.2f}'.format(bestacc))