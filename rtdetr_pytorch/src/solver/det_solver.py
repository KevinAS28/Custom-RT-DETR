'''
by lyuwenyu
'''
import time 
import json
import datetime
import os 
from threading import Thread

import torch 

from src.misc import dist, visualization
from src.data import get_coco_api_from_dataset

from .solver import BaseSolver
from .det_engine import train_one_epoch, evaluate


class DetSolver(BaseSolver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.cfg.enable_backup:
            self.backup_driver = self.cfg.backup_driver
        

    def fit(self, setup_model=True, setup_dataloader=True, run_actual=True):
        print("Start training")
        if os.path.isdir(self.cfg.output_dir) and (not self.cfg.resume):
            print(f'Dir output {self.cfg.output_dir} is exists and the mode is training (not resuming the training). To prevent overriding, training has been cancelled')
            exit(1)
        
        self.train(setup_model, setup_dataloader)
        
        if not os.path.isdir(self.output_dir):
            print(f'Error: output_dir {self.output_dir} cannot be accessed')
            exit(1)

        print(self.model.backbone)

        args = self.cfg 
        
        n_trainable_parameters = sum(p.numel() for p in self.model.parameters() if p.requires_grad)
        n_all_parameters = sum(p.numel() for p in self.model.parameters())
        print('Number of trainable parameters:', n_trainable_parameters)
        print('Number of all parameters:', n_all_parameters)

        if run_actual:
            base_ds = get_coco_api_from_dataset(self.val_dataloader.dataset)
            # best_stat = {'coco_eval_bbox': 0, 'coco_eval_masks': 0, 'epoch': -1, }
            best_stat = {'epoch': -1, }

            start_time = time.time()
            for epoch in range(self.last_epoch + 1, args.epoches):
                epoch_start_time = time.time()
                print(f'Epoch {epoch} started at: {time.ctime()}')

                if dist.is_dist_available_and_initialized():
                    self.train_dataloader.sampler.set_epoch(epoch)
                            
                train_stats = train_one_epoch(
                    self.model, self.criterion, self.train_dataloader, self.optimizer, self.device, epoch,
                    args.clip_max_norm, print_freq=args.log_step, ema=self.ema, scaler=self.scaler)

                self.lr_scheduler.step()
                
                checkpoint_paths = [self.output_dir / 'checkpoint.pth']
                if self.output_dir:    
                    # extra checkpoint before LR drop and every 100 epochs
                    if (epoch + 1) % args.checkpoint_step == 0:
                        checkpoint_paths.append(self.output_dir / f'checkpoint{epoch:04}.pth')
                    for checkpoint_path in checkpoint_paths:
                        dist.save_on_master(self.state_dict(epoch), checkpoint_path)

                module = self.ema.module if self.ema else self.model
                test_stats, coco_evaluator = evaluate(
                    module, self.criterion, self.postprocessor, self.val_dataloader, base_ds, self.device, self.output_dir
                )

                # TODO 
                for k in test_stats.keys():
                    if k in best_stat:
                        best_stat['epoch'] = epoch if test_stats[k][0] > best_stat[k] else best_stat['epoch']
                        best_stat[k] = max(best_stat[k], test_stats[k][0])
                    else:
                        best_stat['epoch'] = epoch
                        best_stat[k] = test_stats[k][0]
                print('best_stat: ', best_stat)


                log_stats = {**{f'train_{k}': v for k, v in train_stats.items()},
                            **{f'test_{k}': v for k, v in test_stats.items()},
                            'epoch': epoch,
                            'n_parameters': n_all_parameters}

                if self.output_dir and dist.is_main_process():
                    with (self.output_dir / "log.txt").open("a") as f:
                        f.write(json.dumps(log_stats) + "\n")

                    # for evaluation logs
                    if coco_evaluator is not None:
                        (self.output_dir / 'eval').mkdir(exist_ok=True)
                        if "bbox" in coco_evaluator.coco_eval:
                            filenames = ['latest.pth']
                            if epoch % 50 == 0:
                                filenames.append(f'{epoch:03}.pth')
                            for name in filenames:
                                torch.save(coco_evaluator.coco_eval["bbox"].eval,
                                        self.output_dir / "eval" / name)
                                
                print('visualize the log...')
                visualization.visualize_train_log(self.output_dir / "log.txt", self.output_dir / 'viz')

                files_to_backups = []
                files_to_backups.extend(checkpoint_paths)
                files_to_backups.append(self.output_dir / "log.txt")
                

                epoch_time = time.time() - epoch_start_time
                epoch_time_str = str(datetime.timedelta(seconds=int(epoch_time)))    
                if self.cfg.enable_backup:
                    self.backup_driver.backup(dirs_backup=[self.output_dir / 'eval', self.output_dir / 'viz'], files_backup=files_to_backups)
                
                print(f'Epoch {epoch} ended at: {time.ctime()} | time used for epoch {epoch}: {epoch_time_str}')

            total_time = time.time() - start_time
            total_time_str = str(datetime.timedelta(seconds=int(total_time)))
            print('Training time {}'.format(total_time_str))


    def val(self, ):
        self.eval()

        base_ds = get_coco_api_from_dataset(self.val_dataloader.dataset)
        
        module = self.ema.module if self.ema else self.model
        test_stats, coco_evaluator = evaluate(module, self.criterion, self.postprocessor,
                self.val_dataloader, base_ds, self.device, self.output_dir)
                
        if self.output_dir:
            dist.save_on_master(coco_evaluator.coco_eval["bbox"].eval, self.output_dir / "eval.pth")
        if self.cfg.enable_backup:
            Thread(target=self.backup_driver.backup, args=[[self.output_dir], [], []]).start()        
        return
