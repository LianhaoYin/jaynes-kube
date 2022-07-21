# Jaynes, A Utility for training ML models on AWS, GCE, SLURM, with or without docker <a href="figures/ETJaynes_defiant.jpg" target="_blank"><img src="figures/ETJaynes_defiant.jpg" alt="Defiant Jaynes" align="right" width="350px" style="top:20px"></a>

[![Downloads](http://pepy.tech/badge/jaynes)](http://pepy.tech/project/jaynes)

## Overview

The reality of ML training in universities is that we use what ever hardware we are given (for free). This means that
we might have a few beefy GPU machines, an HPC cluster, plus some GCE/AWS credits that we get through grants. 
[**Jaynes**](https://github.com/episodeyang/jaynes) is a well-designed python package that makes running across
these inhomogenous hardward resources a pleasure.

**install** (requires unix operating system.)
```bash
pip install jaynes
```
The best way to get started with jaynes is to take a look at one of the example projects in the [[**jaynes-starter-kit**]](https://github.com/geyang/jaynes-starter-kit). For a rough idea, here is how to use jaynes to launch a training function:

To run **locally**:
```python
import jaynes

def training(arg_1, key_arg=None, key_arg_2=None):
    print(f'training is running! (arg_1={arg_1}, key_arg={key_arg})')

jaynes.config(mode="local", arg_1=10, key_arg=0.3)
jaynes.run(training)
jaynes.listen()
```

We recommend setting up a `main` training function with the following sinature:
```python
from params_proto import ParamsProto

class Args(ParamsProto):
    seed = 100
    lr = 3e-4
    # ...
    
def main(**deps):
    from ml_logger import logger
    
    Args._update(deps)
    logger.log_params(Args=vars(Args))
    
    # ... your main training steps
```

This way you can call the main fn directly for local debugging, but launch it as an entry point at scale.

## Setup

Jaynes has gone through a large number of iterations. This version incorporates best practices we learned
from other open-source communities. You can specify a `jaynes.yml` config file ([copy one from our sample
project to get started!](example_projects)) for the type of hosts (ssh/docker/singularity) and launchers
 (ssh/ec2/gce/slurm), so that none of those settings need to appear in your ML python script. When called
from python, Jaynes automatically traverses the file tree to find the root of the project, the 
same way as git.

For example, to run your code on a remote computer via ssh:
```yaml
# your_project/jaynes.yml
version: 0
verbose: true
run:  # this is specific to each launch, and is dynamically overwritten in-memory
  mounts:
    - !mounts.S3Code
      s3_prefix: s3://ge-bair/jaynes-debug
      local_path: .
      host_path: /home/ubuntu/
      container_path: /Users/geyang/learning-to-learn
      pypath: true
      excludes: "--exclude='*__pycache__' --exclude='*.git' --exclude='*.idea' --exclude='*.egg-info'   --exclude='*.pkl'"
      compress: true
  runner:
    !runners.Docker
    name:   # not implemented yet
    image: "episodeyang/super-expert"
    startup: "yes | pip install jaynes ml-logger -q"
    work_directory: "{mounts[0].container_path}"
    ipc: host
  host:
    envs: "LANG=utf-8"
    pre_launch: "pip install jaynes ml-logger -q"
  launch:
    type: ssh
    ip: <your ip address>
    username: ubuntu
    pem: ~/.ssh/your_rsa_key
```

In python (your code):
```python
# your_project/launch.py
import jaynes

def training(arg_1, key_arg=None):
    print(f'training is running! (arg_1={arg_1}, key_arg={key_arg})')

jaynes.run(training)
```

## Using Modes

A lot of times you want to setup a different run **modes** so it is
easy to switch between them during development.

```yaml
# your_project/jaynes.yml
version: 0
mounts: # mount configurations Available keys: NOW, UUID,
  - !mounts.S3Code &code_mount
    s3_prefix: s3://ge-bair/jaynes-debug
    local_path: .
    host_path: /home/ubuntu/jaynes-mounts/{NOW:%Y-%m-%d}/{NOW:%H%M%S.%f}
    # container_path: /Users/geyang/learning-to-learn
    pypath: true
    excludes: "--exclude='*__pycache__' --exclude='*.git' --exclude='*.idea' --exclude='*.egg-info' --exclude='*.pkl'"
    compress: true
hosts:
  hodor: &hodor
    ip: <your ip address>
    username: ubuntu
    pem: ~/.ssh/incrementium-berkeley
runners:
  - !runners.Docker &ssh_docker
    name: "some-job"  # only for docker
    image: "episodeyang/super-expert"
    startup: yes | pip install jaynes ml-logger -q
    envs: "LANG=utf-8"
    pypath: "{mounts[0].container_path}"
    launch_directory: "{mounts[0].container_path}"
    ipc: host
    use_gpu: false
modes: # todo: add support to modes.
  hodor:
    mounts:
      - *code_mount
    runner: *ssh_docker
    launch:
      type: ssh
      <<: *hodor
```

now run in python
```python
# your_project/launch.py
import jaynes

def training(arg_1, key_arg=None):
    print(f'training is running! (arg_1={arg_1}, key_arg={key_arg})')

jaynes.config(mode="hodor")
jaynes.run(training)
```

## ToDos

- [ ] more documentation
- [ ] singularity support
- [ ] GCE support
- [ ] support using non-s3 code repo.

### Done

- [x] get the initial template to work

## Installation

```bash
pip install jaynes
```

## Usage (**Show me the Mo-NAY!! :moneybag::money_with_wings:**)

Check out the [test_projects](example_projects) folder for projects that you can run.

## To Develop

```bash
git clone https://github.com/episodeyang/jaynes.git
cd jaynes
make dev
```

To test, run

```bash
make test
```

This `make dev` command should build the wheel and install it in your current python environment. Take a look at the [./Makefile](./Makefile) for details.

**To publish**, first update the version number, then do:

```bash
make publish
```

## Acknowledgements

This code is inspired by @justinfu's [doodad](https://github.com/justinjfu/doodad), which is in turn built on top of Peter Chen's script.

This code is written from scratch to allow a more permissible open-source license (BSD). Go bears :bear: !!
