import torch
import numpy as np
import torch.nn as nn
import os, copy
import matplotlib.pyplot as plt

from utils import save_checkpoint, get_lr
from tqdm import tqdm
from tensorboardX import SummaryWriter


cuda = torch.cuda.is_available()
device = torch.device("cuda:0" if cuda else "cpu")


def fit(train_loader,
        val_loader,
        model,
        loss_fn,
        optimizer,
        scheduler,
        n_epochs,
        cuda,
        log_interval,
        validation_frequency,
        save_root,
        init,
        writer,
        start_epoch=0):

    best_loss = 100000

    val_x = []
    val_y = []
    val_y_contras = []
    val_y_gn = []

    train_x = []
    train_y = []
    train_y_contras = []
    train_y_gn = []

    iteration = 0
    for epoch in range(start_epoch, n_epochs):
        iteration = epoch*len(train_loader)
        # scheduler.step()
        '''
        UserWarning: Detected call of `lr_scheduler.step()` before `optimizer.step()`. 
        In PyTorch 1.1.0 and later, you should call them in the opposite order: `optimizer.step()` before `lr_scheduler.step()`.  
        Failure to do this will result in PyTorch skipping the first value of the learning rate schedule. 
        See more details at https://pytorch.org/docs/stable/optim.html#how-to-adjust-learning-rate
        '''
        # Train stage
        train_loss, total_contras_loss, total_gnloss, train_triplet_level, train_gn_level, train_e1, train_e2, total_loss_pos_mean_level, total_loss_neg_mean_level = train_epoch(
            val_loader,
            train_loader,
            model,
            loss_fn,
            optimizer,
            cuda,
            log_interval,
            save_root,
            epoch,
            init,
            iteration,
            writer)
        train_x.append(epoch + 1)
        train_y.append(train_loss)
        train_y_contras.append(total_contras_loss)
        train_y_gn.append(total_gnloss)

        scheduler.step()
        message = '\nEpoch: {}/{}. Train set: Average loss: {:.4f}\ttriplet: {:.6f}\tgn loss: {:.6f}'.format(
            epoch + 1, n_epochs, train_loss, total_contras_loss, total_gnloss)
        message += ' Lr:{}'.format(get_lr(optimizer))
        
        # Validate stage
        if val_loader and (epoch % validation_frequency == 0):
            val_loss, val_contras_loss, val_gnloss, val_triplet_level, val_gn_level, val_e1, val_e2 = test_epoch(
                val_loader, model, loss_fn, cuda, epoch, writer)
            val_loss /= len(val_loader)
            val_contras_loss /= len(val_loader)
            val_gnloss /= len(val_loader)
            val_triplet_level = [item / len(val_loader) for item in val_triplet_level]
            val_gn_level = [item / len(val_loader) for item in val_gn_level]
            val_e1 /= len(val_loader)
            val_e2 /= len(val_loader)

            val_x.append(epoch + 1)
            val_y.append(val_loss)
            val_y_contras.append(val_contras_loss)
            val_y_gn.append(val_gnloss)

            message += '\nEpoch: {}/{}. Validation set: Average loss: {:.4f}\ttriplet loss: {:.6f}\tgn loss: {:.6f}'.format(
                epoch + 1, n_epochs, val_loss, val_contras_loss, val_gnloss)

            # save the currently best model
            if val_loss < best_loss:
                best_loss = val_loss
                # best_model_wts = copy.deepcopy(model.state_dict())
                with open(os.path.join(save_root, "best.txt"), 'a+') as f:
                    f.write("{}\n".format(epoch))

                if (epoch+1) >= 20:
                    save_checkpoint(model.state_dict(), optimizer.state_dict(), scheduler.state_dict(), True, save_root, epoch)
                    message += '\nSaving best model ...'

        # save the model for every epochs
        if ((epoch+1) % 1) == 0:
            message += '\nSaving checkpoint ... \n'
            save_checkpoint(model.state_dict(), optimizer.state_dict(), scheduler.state_dict(), False, save_root, epoch)
        print(message)

        # plot the loss curve
        plt.figure(figsize=(12, 8))
        plt.subplot(2, 1, 1)
        plt.title("train_val_loss_pic")
        # plt.xlabel("epoch")
        # plt.ylabel("loss_value")
        plt.plot(val_x, val_y, "-s", label='val_total')
        plt.plot(train_x, train_y, "+-", label='train_total')
        plt.legend(bbox_to_anchor=(1.0, 1), loc=1, borderaxespad=0.)

        plt.subplot(2, 2, 3)
        plt.title("triplet_loss")
        # plt.xlabel("epoch")
        # plt.ylabel("loss_value")
        plt.plot(val_x, val_y_contras, "-s", label='val_triplet')
        plt.plot(train_x, train_y_contras, "+-", label='train_triplet')
        # plt.legend(bbox_to_anchor=(1.0, 1), loc=1, borderaxespad=0.)

        plt.subplot(2, 2, 4)
        plt.title("gn_loss")
        # plt.xlabel("epoch")
        # plt.ylabel("loss_value")
        plt.plot(val_x, val_y_gn, "-s", label='val_gn')
        plt.plot(train_x, train_y_gn, "+-", label='train_gn')
        # plt.legend(bbox_to_anchor=(1.0, 1), loc=1, borderaxespad=0.)
        plt.savefig("./train_val_loss_pic.png")
        plt.close()


def train_epoch(val_loader, train_loader, model, loss_fn, optimizer, cuda,
                log_interval, save_root, epoch, init, iteration, writer):
    # initialize network parameters, oscillates a lot here, not good
    if init and epoch == 0:
        for m in model.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.xavier_normal_(m.weight.data)
                m.bias.data.fill_(0)

    model.train()

    # added
    total_tripletloss_level = [0,0,0,0]
    total_gnloss_level = [0,0,0,0]
    total_loss_pos_mean_level = [0,0,0,0]
    total_loss_neg_mean_level = [0,0,0,0]

    total_loss = 0
    total_contras_loss = 0
    total_gnloss = 0
    total_e1 = 0
    total_e2 = 0

    imgA = []
    imgB = []
    loader = tqdm(train_loader)
    for batch_idx, (img_ab, corres_ab) in enumerate(loader):
        corres_ab = corres_ab if len(corres_ab) > 0 else None
        if not type(img_ab) in (tuple, list):
            img_ab = (img_ab, )
        if cuda:
            img_ab = tuple(d.to(device) for d in img_ab)
            if corres_ab is not None:
                corres_ab = {
                    key: corres_ab[key].to(device)
                    for key in corres_ab
                }

        # if (iteration+1) % 2 == 0:
        #     optimizer.zero_grad()
        optimizer.zero_grad()
        
        outputs = model(*img_ab)

        if type(outputs) not in (tuple, list):
            outputs = (outputs, )

        loss_inputs = outputs
        if corres_ab is not None:
            corres_ab = (corres_ab, )
            loss_inputs += corres_ab
        
        # modified for triplet loss negative part
        loss_inputs += (iteration, ) # modified from epoch
        iteration += 1
        # modified to print gn loss seperately
        loss_inputs += (True, )

        loss_outputs, contras_loss_outputs, gnloss_outputs, tripletloss_level, gnloss_level, e1, e2, loss_pos_mean_level, loss_neg_mean_level = loss_fn(*loss_inputs)
        loss = loss_outputs[0] if type(loss_outputs) in (
            tuple, list) else loss_outputs
        contras_loss = contras_loss_outputs[0] if type(
            contras_loss_outputs) in (tuple, list) else contras_loss_outputs
        gnloss = gnloss_outputs[0] if type(gnloss_outputs) in (
            tuple, list) else gnloss_outputs

        total_loss += loss.item()
        total_contras_loss += contras_loss.item()
        total_gnloss += gnloss.item()
        total_e1 += e1.item()
        total_e2 += e2.item()

        loader.set_description("Iteration: {}, Train loss: {:.4f}, triplet: {:.6f}, gn: {:.6f}".format(iteration, total_loss / (batch_idx + 1), total_contras_loss / (batch_idx + 1), total_gnloss / (batch_idx + 1)))
        loader.refresh()
        
        # uncomment if log the train loss per iteration
        # writer.add_scalar('train_total_loss_per_iter', total_loss / (batch_idx + 1), iteration)
        # writer.add_scalar('train_triplet_loss_per_iter', total_contras_loss / (batch_idx + 1), iteration)
        # writer.add_scalar('train_gn_loss_per_iter', total_gnloss / (batch_idx + 1), iteration)
        
        for i in range(4):
            total_tripletloss_level[i] += tripletloss_level[i]
            total_gnloss_level[i] += gnloss_level[i]
            total_loss_pos_mean_level[i] += loss_pos_mean_level[i]
            total_loss_neg_mean_level[i] += loss_neg_mean_level[i]

        loss.backward()
        # if iteration % 2 == 0:
        #     optimizer.step()
        optimizer.step()

        del img_ab
        del corres_ab
        torch.cuda.empty_cache()

    total_loss /= (batch_idx + 1)
    total_contras_loss /= (batch_idx + 1)
    total_gnloss /= (batch_idx + 1)
    total_tripletloss_level = [item / (batch_idx + 1) for item in total_tripletloss_level]
    total_gnloss_level = [item / (batch_idx + 1) for item in total_gnloss_level]
    total_loss_pos_mean_level= [item / (batch_idx + 1) for item in total_loss_pos_mean_level]
    total_loss_neg_mean_level= [item / (batch_idx + 1) for item in total_loss_neg_mean_level]
    total_e1 /= (batch_idx + 1)
    total_e2 /= (batch_idx + 1)

    # uncomment if log the train loss per epoch
    writer.add_scalar('train_total_loss', total_loss, epoch)
    writer.add_scalar('train_triplet_loss', total_contras_loss, epoch)
    writer.add_scalar('train_gn_loss', total_gnloss, epoch)

    return total_loss, total_contras_loss, total_gnloss, total_tripletloss_level, total_gnloss_level, total_e1, total_e2, total_loss_pos_mean_level, total_loss_neg_mean_level


def test_epoch(val_loader, model, loss_fn, cuda, epoch, writer):
    with torch.no_grad():
        model.eval()
        val_loss = 0
        val_contras_loss = 0
        val_gnloss = 0
        val_e1 = 0
        val_e2 = 0
        # added
        total_tripletloss_level = [0,0,0,0]
        total_gnloss_level = [0,0,0,0]

        imgA = []
        imgB = []

        for batch_idx, (img_ab, corres_ab) in enumerate(val_loader):
            corres_ab = corres_ab if len(corres_ab) > 0 else None
            if not type(img_ab) in (tuple, list):
                img_ab = (img_ab, )
            if cuda:
                img_ab = tuple(d.to(device) for d in img_ab)
                if corres_ab is not None:
                    corres_ab = {
                        key: corres_ab[key].to(device)
                        for key in corres_ab
                    }

            outputs = model(*img_ab)

            if type(outputs) not in (tuple, list):
                outputs = (outputs, )
            loss_inputs = outputs
            if corres_ab is not None:
                corres_ab = (corres_ab, )
                loss_inputs += corres_ab

            # modified for triplet loss negative part
            loss_inputs += (epoch, )
            # modified to print gn loss seperately
            loss_inputs += (False, )

            loss_outputs, contras_loss_outputs, gnloss_outputs, tripletloss_level, gnloss_level, e1, e2, loss_pos_mean_level, loss_neg_mean_level = loss_fn(
                *loss_inputs)
            loss = loss_outputs[0] if type(loss_outputs) in (
                tuple, list) else loss_outputs
            contras_loss = contras_loss_outputs[0] if type(
                contras_loss_outputs) in (tuple,
                                          list) else contras_loss_outputs
            gnloss = gnloss_outputs[0] if type(gnloss_outputs) in (
                tuple, list) else gnloss_outputs

            val_loss += loss.item()
            val_contras_loss += contras_loss.item()
            val_gnloss += gnloss.item()
            val_e1 += e1.item()
            val_e2 += e2.item()

            for i in range(4):
                total_tripletloss_level[i] += tripletloss_level[i]
                total_gnloss_level[i] += gnloss_level[i]

    # uncomment if log the validation loss per epoch
    writer.add_scalar('val_total_loss', val_loss, epoch)
    writer.add_scalar('val_triplet_loss', val_contras_loss, epoch)
    writer.add_scalar('val_gn_loss', val_gnloss, epoch)

    return val_loss, val_contras_loss, val_gnloss, tripletloss_level, gnloss_level, val_e1, val_e2
