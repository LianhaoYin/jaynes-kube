import base64
import glob
import os
import tempfile
from textwrap import dedent
from types import SimpleNamespace
from typing import Union

from .helpers import cwd_ancestors, omit, hydrate
from .templates import ec2_terminate, ssh_remote_exec
from .runners import Docker, Simple
from .shell import ck, popen


class Jaynes:
    def __init__(self, mounts=None, runner=None):
        self.mounts = mounts or []
        self.set_runner(runner)

    def set_runner(self, runner: Union[Docker, Simple]):
        self.runner = runner

    def set_mount(self, *mounts):
        self.mounts = mounts

    _uploaded = []

    def upload_mount(self, verbose=None):
        for mount in self.mounts:
            if mount in self._uploaded:
                print('this package is already uploaded')
            else:
                self._uploaded.append(mount)
                ck(mount.local_script, verbose=verbose, shell=True)

    # def run_local_setup(self, verbose=False):
    #     for m in self.mounts:
    #         self.upload_mount(m)

    def launch_local_docker(self, log_dir="/tmp/jaynes-mount", delay=None, verbose=False, dry=False):
        # the log_dir is primarily used for the run script. Therefore it should use ued here instead.
        log_path = os.path.join(log_dir, "jaynes-launch.log")
        error_path = os.path.join(log_dir, "jaynes-launch.err.log")

        upload_script = '\n'.join(
            [m.upload_script for m in self.mounts if hasattr(m, "upload_script") and m.upload_script]
        )
        remote_setup = "\n".join(
            [m.remote_setup for m in self.mounts if hasattr(m, "remote_setup") and m.remote_setup]
        )

        remote_script = dedent(f"""
        #!/bin/bash
        # to allow process substitution
        set +o posix
        mkdir -p {log_dir}
        {{
            # clear main_log
            truncate -s 0 {log_path}
            truncate -s 0 {error_path}
            
            # remote_setup
            {remote_setup}

            # upload_script
            {upload_script}

            {self.runner.setup_script}
            {self.runner.run_script}
            
            # Now sleep before ending this script
            sleep {delay}
        }} > >(tee -a {log_path}) 2> >(tee -a {error_path} >&2)
        """).strip()
        if verbose:
            print(remote_script)
        if not dry:
            ck(remote_script, shell=True)
        return self

    def make_host_script(self, log_dir, setup=None, terminate_after=False, delay=None,
                         instance_tag=None, region=None):
        """
        function to make the host script

        :param log_dir: 
        :param sudo:
        :param terminate_after:
        :param delay:
        :param instance_tag:
        :param region:
        :return:
        """
        log_path = os.path.join(log_dir, "jaynes-launch.log")
        error_path = os.path.join(log_dir, "jaynes-launch.err.log")

        upload_script = '\n'.join(
            [m.upload_script for m in self.mounts if hasattr(m, "upload_script") and m.upload_script]
        )
        remote_setup = "\n".join(
            [m.host_setup for m in self.mounts if hasattr(m, "host_setup") and m.host_setup]
        )
        if instance_tag:
            assert len(instance_tag) <= 128, "Error: aws limits instance tag to 128 unicode characters."

        setup = setup or f"""
            if ! type aws > /dev/null; then
                pip install awscli --upgrade --user
            fi
        """
        if instance_tag:
            assert region, "region need to be specified if instance tag is given."
        if terminate_after:
            assert region, "region need to be specified if instance is self-terminating."

        tag_current_instance = f"""
            if [ `cat /sys/devices/virtual/dmi/id/bios_version` == 1.0 ] || [[ -f /sys/hypervisor/uuid && `head -c 3 /sys/hypervisor/uuid` == ec2 ]]; then
                echo "Is EC2 Instance"
                EC2_INSTANCE_ID="`wget -q -O - http://169.254.169.254/latest/meta-data/instance-id`"
                aws ec2 create-tags --resources $EC2_INSTANCE_ID --tags 'Key=Name,Value={instance_tag}' --region {region}
                aws ec2 create-tags --resources $EC2_INSTANCE_ID --tags 'Key=exp_prefix,Value={instance_tag}' --region {region};
            fi
        """
        # TODO: path.join is running on local computer, so it might not be quite right if remote is say windows.
        # note: dedent is required by aws EC2.
        # noinspection PyAttributeOutsideInit
        self.launch_script = dedent(f"""
        #!/bin/bash
        # to allow process substitution
        set +o posix
        mkdir -p {log_dir}
        JAYNES_LOG_DIR={log_dir}
        {{
            # clear main_log
            truncate -s 0 {log_path}
            truncate -s 0 {error_path}
            
            {setup}
            {tag_current_instance if instance_tag else ""}
            
            # remote_setup
            {remote_setup}
            # upload_script
            {upload_script}

            # todo: include this inside the runner script.
            {self.runner.setup_script}
            {self.runner.run_script}
            {self.runner.post_script}
            {ec2_terminate(region, delay) if terminate_after else ""}

        }} > >(tee -a {log_path}) 2> >(tee -a {error_path} >&2)
        """).strip()

        return self

    def launch_ssh(self, ip, port=None, username="ubuntu", pem=None, sudo=False,
                   detached=True, dry=False, verbose=False):
        """
        run launch_script remotely by ip_address. First saves the run script locally as a file, then use
        scp to transfer the script to remote instance then run.

        :param sudo:
        :param username:
        :param ip:
        :param port:
        :param pem:
        :param detached: use call instead of checkcall, allowing the python program to continue execution w/o
                         blocking. Should the default.
        :param dry:
        :param verbose:
        :return:
        """
        tf = tempfile.NamedTemporaryFile(prefix="jaynes_launcher-", suffix=".sh", delete=False)
        with open(tf.name, 'w') as f:
            script_name = os.path.basename(tf.name)
            # note: kill requires sudo
            f.write(self.launch_script + "\n"
            f"sudo kill $(ps aux | grep '{script_name}' | awk '{{print $2}}')\n"
            f"echo 'clean up all startup script processes'\n")
        tf.file.close()

        upload_script, launch = ssh_remote_exec(username, ip, tf.name, port=port, pem=pem, sudo=sudo, )

        if not dry:
            if upload_script:
                # done: separate out the two commands
                ck(upload_script, verbose=verbose, shell=True)
            if detached:
                import sys
                popen(launch, verbose=verbose, shell=True, stdout=sys.stdout, stderr=sys.stderr)
            else:
                ck(launch, verbose=verbose, shell=True)

        elif verbose:
            if upload_script:
                print(upload_script)
            print(launch)

        import time
        time.sleep(0.1)
        os.remove(tf.name)

    def launch_ec2(self, region, image_id, instance_type, key_name, security_group, spot_price=None,
                   iam_instance_profile_arn=None, verbose=False, dry=False):
        import boto3
        ec2 = boto3.client("ec2", region_name=region, aws_access_key_id=os.environ.get('AWS_ACCESS_KEY'),
                           aws_secret_access_key=os.environ.get('AWS_ACCESS_SECRET'))

        instance_config = dict(ImageId=image_id, KeyName=key_name, InstanceType=instance_type,
                               SecurityGroups=(security_group,),
                               IamInstanceProfile=dict(Arn=iam_instance_profile_arn))
        if spot_price:
            # for detailed settings see:
            #     http://boto3.readthedocs.io/en/latest/reference/services/ec2.html#EC2.Client.request_spot_instances
            # issue here: https://github.com/boto/boto3/issues/368
            instance_config.update(UserData=base64.b64encode(self.launch_script.encode()).decode("utf-8"))
            response = ec2.request_spot_instances(InstanceCount=1, LaunchSpecification=instance_config,
                                                  SpotPrice=str(spot_price), DryRun=dry)
            spot_request_id = response['SpotInstanceRequests'][0]['SpotInstanceRequestId']
            if verbose:
                print(response)
            return spot_request_id
        else:
            instance_config.update(UserData=self.launch_script)
            response = ec2.run_instances(MaxCount=1, MinCount=1, **instance_config, DryRun=dry)
            if verbose:
                print(response)
            return response

    # aliases of launch scripts
    local_docker = launch_local_docker
    ssh = launch_ssh
    ec2 = launch_ec2


import yaml
from termcolor import cprint
from . import mounts, runners
from datetime import datetime
from uuid import uuid4


class RUN:
    project_root = None
    raw = None
    J: Jaynes = None
    config = None

    # default value for the run mode
    mode = None


def config(mode=None, *, config_path=None, runner=None, host=None, launch=None, **ext):
    """
    Configuration function for Jaynes

    :param mode: the run mode you want to use, specified under the `modes` key inside your jaynes.yml config file
    :param config_path: the path to the configuration file. Allows you to use a custom configuration file
    :param runner: configuration for the runner, overwrites what's in the jaynes.yml file.
    :param host: configuration for the host machine, overwrites what's in the jaynes.yml file.
    :param launch: configuration for the `launch` function, overwrites what's in the jaynes.yml file
    :param ext: variables to pass into the string interpolation. Shows up directly as root-level variables in
                the string interpolation context
    :return: None
    """
    RUN.mode = mode

    ctx = dict(env=SimpleNamespace(**os.environ), now=datetime.now(), uuid=uuid4(), **ext)

    if RUN.J is None:
        if config_path is None:
            for d in cwd_ancestors():
                try:
                    config_path, = glob.glob(d + "/jaynes.yml")
                    break
                except Exception:
                    pass
        if config_path is None:
            cprint('No `jaynes.yml` is found. Run `jaynes.init` to create a configuration file.', "red")
            return

        RUN.project_root = os.path.dirname(config_path)

        from inspect import isclass

        for k, c in mounts.__dict__.items():
            if isclass(c):
                yaml.SafeLoader.add_constructor("!mounts." + k, hydrate(c, ctx), )

        for k, c in runners.__dict__.items():
            if hasattr(c, 'from_yaml'):
                yaml.SafeLoader.add_constructor("!runners." + k, c.from_yaml)

        with open(config_path, 'r') as f:
            raw = yaml.safe_load(f)

        # order or precendence: mode -> run -> root
        RUN.raw = raw
        RUN.J = Jaynes()

    RUN.config = RUN.raw.copy()
    if mode == 'local':
        cprint("running local mode", "green")
        return

    elif not mode:
        RUN.config.update(RUN.raw.get('run', {}))
    else:
        modes = RUN.raw.get('modes', {})
        RUN.config.update(modes[mode])

    if runner:
        Runner, runner_config = RUN.config['runner']
        updated = runner_config.copy()
        updated.update(runner)
        RUN.config['runner'] = Runner, updated

    if launch:
        updated = RUN.config['launch']
        updated.update(launch)
        RUN.config["launch"] = updated

    if host:
        updated = RUN.config['host']
        updated.update(host)
        RUN.config["host"] = updated

    RUN.config.update(ctx)
    RUN.J.set_mount(*RUN.config.get("mounts"))
    RUN.J.upload_mount(verbose=RUN.config.get('verbose'))


def run(fn, *args, __run_config=None, **kwargs, ):
    from copy import deepcopy
    if not RUN.J:
        config()

    if RUN.mode == "local":
        return fn(*args, **kwargs)

    # config.RUNNER
    Runner, runner_kwargs = RUN.config.get('runner')
    # interpolaiton context
    context = RUN.config.copy()
    context['run'] = SimpleNamespace(
        cwd=os.getcwd(),
        now=datetime.now(),
        uuid=uuid4(),
        pypaths=SimpleNamespace(
            host=":".join([m.host_path for m in RUN.config['mounts'] if m.pypath]),
            container=":".join([m.container_path for m in RUN.config['mounts'] if m.pypath])
        ), **(__run_config or {}))
    # todo: mapping current work directory correction on the remote instance.

    _ = {k: v.format(**context) if type(v) is str else v for k, v in runner_kwargs.items()}
    if 'launch_directory' not in _:
        _['launch_directory'] = os.getcwd()

    j = deepcopy(RUN.J)
    j.set_runner(Runner(**_, mount=" ".join([m.docker_mount for m in RUN.config['mounts']]), ))
    j.runner.run(fn, *args, **kwargs)

    # config.HOST
    host_config = RUN.config.get('host', {})
    j.make_host_script(log_dir="~/debug-outputs", **host_config)

    if RUN.config.get('verbose'):
        print(j.launch_script)

    # config.LAUNCH
    launch_config = RUN.config['launch']
    _ = getattr(j, launch_config['type'])(**omit(launch_config, 'type'))
    if RUN.config.get('verbose'):
        cprint(f"launched! {_}", "green")
    return _


def listen(timeout=None):
    """Just a for-loop, to keep ths process connected to the ssh session"""
    import math, time
    if timeout:
        time.sleep(timeout)
        cprint(f'jaynes.listen(timeout={timeout}) is now timed out. remote routine is still running.', 'green')
    else:
        while True:
            time.sleep(math.pi * 20)
            cprint('Listening to pipe back...', 'green')
