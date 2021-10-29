#!/bin/bash
echo "Got to run"
sbatch passiv-de-lstm-2layers.slurm
sbatch passiv-de-transformer-2layers.slurm
sbatch passiv-de-transformer-1layer.slurm
sbatch passiv-en-lstm-1layer.slurm
sbatch passiv-en-lstm-2layers.slurm
sbatch passiv-en-transformer-2layers.slurm
sbatch passiv-en-transformer-1layer.slurm
sbatch qform-de-lstm-1layer.slurm
sbatch qform-de-lstm-2layers.slurm
sbatch qform-de-transformer-2layers.slurm
sbatch qform-de-transformer-1layer.slurm
sbatch qform-en-lstm-1layer.slurm
sbatch qform-en-lstm-2layers.slurm
sbatch qform-en-transformer-2layers.slurm
sbatch qform-en-transformer-1layer.slurm
