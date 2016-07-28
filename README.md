# Mist Cloudify Kubernetes Cluster Example


This repository contains a blueprint for installing a kubernetes cluster through mist.io.<br>
The aforementioned kubernetes cluster consists of:

- A kubernetes master
- A kubernetes worker

Before you begin it's recommended you familiarize yourself with the
[Cloudify Terminology](http://getcloudify.org/guide/3.1/reference-terminology.html).<br>
You will also need a [mist.io](https://mist.io/) account.

This has been succesfully tested on CoreOS and Ubuntu 14.04 images under python 2.7.

**Note: Documentation about the blueprints' content is located inside the blueprint files themselves.
Presented here are only instructions on how to run the blueprints using the Cloudify CLI & Mist.io plugin.**

## Step 1: Install the software

```
git clone https://github.com/mistio/kubernetes-blueprint
cd kubernetes-blueprint
virtualenv .  # create virtualenv
source bin/activate
./bin/pip install -r dev-requirements.txt  # install dependencies
./bin/pip install cloudify https://github.com/mistio/mist.client/archive/master.zip
git clone https://github.com/mistio/cloudify-mist-plugin
cd cloudify-mist-plugin
./bin/python setup.py  develop
cd ..
```

## Step 2: Initialize the environment

First of all, you need to add a cloud to your mist.io account. Login to the [dashboard](https://mist.io) and click "ADD CLOUD".
Once you have completed the aforementioned step, retrieve your cloud's ID by clicking on the tile containing your cloud's name
on the mist.io home screen. The ID will be used later on as part of the required blueprint inputs.

You will also need  an SSH key. Visit the Keys tab and generate/upload a key. You can use separate keys for each machine.
Once again, note the name/ID of the newly created SSH key, as it will be by our inputs file.

Now visit your [account page](https://mist.io/account) and create a token under the API TOKENS tabs.

Now, check the inputs files (in .yaml format) under the inputs directory section in order to use them as a guide to fill in the fields accordingly.

Here's a sample:<br>
```
mist_token: 544be89e3016f2fb0ba433802ee432da2e672cd7fe8e4ccc6926e1a54e835eec
mist_key_master: c4b6efa8d0a74f989d2d8a0fae7a04d1
mist_key_worker: c4b6efa8d0a74f989d2d8a0fae7a04d1
mist_cloud_1: 1b2edcb11e524e2aa5fdd89cf1e24278
mist_image_1: ami-d0e21bb1
mist_size_1: m1.medium
mist_location_1: '0'
coreos: true
worker_name: KubernetesWorker
master_name: KubernetesMaster
```

<br>Afterwards, run:

`./bin/cfy local init -p blueprint.yaml -i inputs/<file_name>.yaml`<br>

This command will initialize your working directory with the given blueprint.

The output would be something like this:<br>
```
(kubernetes-blueprint)user@user:~/kubernetes-blueprint$ cfy local init -p blueprint.yaml -i inputs/mist.yaml
Processing Inputs Source: inputs/mist_ec2.yaml
Initiated blueprint.yaml
If you make changes to the blueprint, run 'cfy local init -p mist-blueprint.yaml' again to apply them
```

<br>Now, you can run any type of workflows using your blueprint.<br>

## Step 2: Install a kubernetes cluster

You are now ready to run the `install` workflow:<br>

`./bin/cfy local execute -w install`

This command will deploy a kubernetes master and a kubernetes worker on the specified cloud via mist.io.

The output should be something like:<br>
```
(kubernetes-blueprint)user@user:~/kubernetes-blueprint$ ./bin/cfy local execute -w install
2016-05-08 16:43:48 CFY <local> Starting 'install' workflow execution
2016-05-08 16:43:48 CFY <local> [key_13e52] Creating node
2016-05-08 16:43:48 CFY <local> [master_677f6] Creating node
2016-05-08 16:43:48 CFY <local> [master_677f6.create] Sending task 'plugin.kubernetes.create'
...
2016-05-08 16:52:43 CFY <local> [worker_7a12b.start] Task succeeded 'plugin.kubernetes.start'
2016-05-08 16:52:44 CFY <local> 'install' workflow execution succeeded

```

This will take a while (approximately 10 minutes) to be fully executed. At the end, you will have a kubernetes cluster with two nodes.

As soon as the installation has been succesffully completed, you should see your newly created VMs on the
[mist.io machines page](https://mist.io/#/machines).<br>

At this point, you may specify the command `./bin/cfy local outputs` in order to retrieve the blueprint's outputs, which consist of a
dashboard URL (alongide the required credentials) you may visit in order to verify the deployment of your kubernetes cluster and further
explore it, as well as a `kubectl` command you may run directly in your shell. In case you do not have `kubectl` installed, simply run:<br>
`curl -O https://storage.googleapis.com/kubernetes-release/release/v1.1.8/bin/linux/amd64/kubectl && chmod +x kubectl`.

## Step 3: Scale cluster

To scale the cluster up first edit the `inputs/new_worker.yaml` file with the proper inputs.
Edit the `delta` parameter to specify the number of machines to be added to the cluster.
A positive number denotes an increase of kubernetes workers, while a negative number denotes a decrease of instances.
As soon as you are done editing the inputs file, run:<br>

`./bin/cfy local execute -w scale_cluster -p inputs/new_worker.yaml `

A sample output would be:<br>

```
(kubernetes-blueprint)user@user:~/kubernetes-blueprint$ ./bin/cfy local execute -w scale_cluster -p inputs/new_worker.yaml
Processing Inputs Source: inputs/new_worker.yaml
2016-05-08 17:15:25 CFY <local> Starting 'scale_cluster' workflow execution
...
2016-05-08 17:18:33 LOG <local> INFO:
2016-05-08 17:18:33 LOG <local> INFO:
2016-05-08 17:18:33 LOG <local> INFO: Kubernetes worker 'NewKubernetesWorker' installation script succeeded
2016-05-08 17:18:33 LOG <local> INFO: Upscaling kubernetes cluster succeeded
2016-05-08 17:18:33 CFY <local> 'scale_cluster' workflow execution succeeded
```

You may verify that the nodes were created and successfully added to the cluster by either running the `kubectl`
command or visiting the kubernetes dashboard (both included in the blueprint's outputs section).<br>

To scale the cluster down edit the `inputs/remove_worker.yaml` file and specify the delta parameter as to how many
machines should be removed (destroyed) from the cluster. Then, run:<br>

`./bin/cfy local execute -w scale_cluster -p inputs/remove_worker.yaml`

## Step 4: Uninstall

To uninstall the kubernetes cluster and destroy all the machines run the `uninstall` workflow:<br>

`./bin/cfy local execute -w uninstall`
