#!/bin/bash
#SBATCH --job-name=qform-en-lstm-2layers-70epochs
#SBATCH --open-mode=append
#SBATCH --output=./out_and_err/%j_%x.out
#SBATCH --error=./out_and_err/%j_%x.err
#SBATCH --export=ALL
#SBATCH --time=15:00:00
#SBATCH --gres=gpu:1
#SBATCH --mem=64G
#SBATCH -c 4

singularity exec --nv --overlay $SCRATCH/overlay-25GB-500K.ext3:ro /scratch/work/public/singularity/cuda10.1-cudnn7-devel-ubuntu18.04-20201207.sif /bin/bash -c "

source /ext3/env.sh
conda activate

python ./train.py experiment=qform-en-lstm-2layers-70epochs
"
