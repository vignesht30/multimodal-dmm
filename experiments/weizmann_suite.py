"""Train and compare methods on a suite of inference tasks (Weizmann)."""
from __future__ import division
from __future__ import print_function
from __future__ import absolute_import

import os, argparse, yaml

import pandas as pd
import ray
import ray.tune as tune

from weizmann import WeizmannTrainer
from .analysis import ExperimentAnalysis

parser = argparse.ArgumentParser(formatter_class=
                                 argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('--analyze', action='store_true', default=False,
                    help='analyze without running experiments')
parser.add_argument('--n_repeats', type=int, default=1, metavar='N',
                    help='number of repetitions per config set')
parser.add_argument('--trial_cpus', type=int, default=1, metavar='N',
                    help='number of CPUs per trial')
parser.add_argument('--trial_gpus', type=int, default=0, metavar='N',
                    help='number of GPUs per trial')
parser.add_argument('--max_cpus', type=int, default=None, metavar='N',
                    help='max CPUs for all trials')
parser.add_argument('--max_gpus', type=int, default=None, metavar='N',
                    help='max GPUs for all trials')
parser.add_argument('--local_dir', type=str, default="./",
                help='path to Ray results')
parser.add_argument('--exp_name', type=str, default="weizmann_semisup",
                    help='experiment name')
parser.add_argument('--config', type=yaml.safe_load, default={},
                    help='trial configuration arguments')

def run(args):
    """Runs Ray experiments."""
    # If max resources not specified, default to maximum - 1
    if args.max_cpus is None:
        import psutil
        args.max_cpus = min(1, psutil.cpu_count() - 1)
    if args.max_gpus is None:
        import torch
        args.max_gpus = min(1, torch.cuda.device_count() - 1)
    
    ray.init(num_cpus=args.max_cpus, num_gpus=args.max_gpus)

    # Convert data dir to absolute path so that Ray trials can find it
    data_dir = os.path.abspath(WeizmannTrainer.defaults['data_dir'])

    # Set up trial configuration
    config = {
        "data_dir": data_dir,
        "epochs" : 500,
        "kld_anneal" : 250,
        # Save less to consume less disk space
	"save_freq": 50,
        # Set low learning rate to prevent NaNs
        "lr": 5e-4,
        # Only train on videos, masks, and actions
        "modalities": ['video', 'mask', 'action'],
        # Do not provide masks or action labels for test set
        "drop_mods": ['mask', 'action'],
        # Repeat each configuration with different random seeds
        "seed": tune.grid_search(range(args.n_repeats)),
        # Iterate across inference methods
        "method": tune.grid_search(['bfvi', 'b-mask', 'f-mask',
                                    'b-skip', 'f-skip'])
    }

    # Update config with parameters from command line
    config.update(args.config)

    # Register trainable and run trials
    trainable = lambda c, r : WeizmannTrainer.tune(c, r)
    tune.register_trainable("weizmann_tune", trainable)

    trials = tune.run(
        "weizmann_tune",
        name=args.exp_name,
        config=config,
        local_dir=args.local_dir,
        resources_per_trial={"cpu": args.trial_cpus, "gpu": args.trial_gpus}
    )

def analyze(args):
    """Analyzes experiment results in log directory."""
    exp_dir = os.path.join(args.local_dir, args.exp_name)
    ea = ExperimentAnalysis(exp_dir)
    df = ea.dataframe().sort_values(['trial_id'])
    best_results = {'method': [], 'loss': [],
                    'ssim': [], 'm_ssim': [], 'action': []}
    
    # Iterate across trials
    for i, trial in df.iterrows():
        print("Trial:", trial['experiment_tag'])
        try:
            trial_df = ea.trial_dataframe(trial['trial_id'])
        except(ValueError, pd.errors.EmptyDataError):
            print("No progress data to read for trial, skipping...")
            continue
        method = trial['config:method']
        best_idx = trial_df.mean_loss.idxmin() 
        best_loss, best_ssim, best_m_ssim, best_act_acc =\
            trial_df[['mean_loss', 'ssim', 'm_ssim', 'action']].iloc[best_idx]
        print("Best loss:", best_loss)
        print("Best video SSIM:", best_ssim)
        print("Best mask SSIM:", best_m_ssim)
        print("Best action acc.:", best_act_acc)
        print("---")

        # Store best results for each trial
        best_results['method'].append(method)
        best_results['loss'].append(best_loss)
        best_results['ssim'].append(best_ssim)
        best_results['m_ssim'].append(best_m_ssim)
        best_results['action'].append(best_act_acc)

    # Average results for each method
    best_results = pd.DataFrame(best_results).sort_values(by='method')
    best_results = best_results.groupby('method').mean()
    print('--Mean--')
    print(best_results)

    # Save results to CSV file
    best_results.to_csv(os.path.join(exp_dir, 'best_results.csv'), index=False)
        
if __name__ == "__main__":
    args = parser.parse_args()
    if not args.analyze:
        run(args)
    analyze(args)

