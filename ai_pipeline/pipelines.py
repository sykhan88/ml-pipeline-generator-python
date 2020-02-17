# python3
# Copyright 2020 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Pipeline class definitions."""
import abc
import json

import datetime as dt
import jinja2 as jinja


class _Component(object):
    """A BasePipeline component (behaves like a tree)."""

    def __init__(self, role, comp_id, params=None):
        self.role = role
        self.id = comp_id
        # TODO(humichael): support params
        self.params = params if params else {}
        self.children = []

    def add_child(self, comp):
        self.children.append(comp)


class BasePipeline(abc.ABC):
    """Abstract class representing an ML pipeline."""

    def __init__(self, model):
        self.model = model
        self.structure = _Component("start", -1)
        self.size = 0

        now = dt.datetime.now().strftime("%y%m%d_%h%m%s")
        self.job_id = "{}_{}".format(self.model.model["name"], now)

    def add_train_component(self, parent=None, wait_interval=None):
        """Adds a train component after the specified parent."""
        if not parent:
            parent = self.structure
        params = {
            "wait_interval": wait_interval,
        }
        params = {k: v for k, v in params.items() if v is not None}

        component = _Component("train", self.size, params=params)
        parent.add_child(component)
        self.size += 1
        return component

    def add_deploy_component(self, parent=None, model_uri=None,
                             wait_interval=None):
        """Adds a deploy component after the specified parent."""
        if not parent:
            parent = self.structure
        params = {
            "model_uri": model_uri,
            "wait_interval": wait_interval,
        }
        params = {k: v for k, v in params.items() if v is not None}

        component = _Component("deploy", self.size, params=params)
        parent.add_child(component)
        self.size += 1
        return component

    def add_predict_component(self, parent=None, model=None, version=None):
        """Adds a predict component after the specified parent."""
        if not parent:
            parent = self.structure
        params = {
            "model_id": model,
            "version_id": version,
        }
        params = {k: v for k, v in params.items() if v is not None}

        component = _Component("predict", self.size, params=params)
        parent.add_child(component)
        self.size += 1
        return component

    def print_structure(self):
        """Prints the structure of the pipeline."""
        next_comps = [self.structure]
        while next_comps:
            comp = next_comps.pop()
            if comp.id != -1:
                print(comp.id, [x.id for x in comp.children])
            next_comps.extend(comp.children)

    def to_graph(self):
        """Represents the pipeline as edges and vertices.

        Returns:
            components: the vertices of the graph.
            relations: the edges of the graph in (parent, child) form.
        """
        components = [None] * self.size
        relations = []
        next_comps = [self.structure]
        while next_comps:
            comp = next_comps.pop()
            next_comps.extend(comp.children)
            if comp.id != -1:
                components[comp.id] = comp
            for child in comp.children:
                relations.append((comp.id, child.id))
        return components, relations

    @abc.abstractmethod
    def generate_pipeline(self):
        """Creates the files to compile a pipeline."""
        pass


class KfpPipeline(BasePipeline):
    """KubeFlow Pipelines class."""

    def _get_train_params(self):
        """Returns parameters for training on CAIP."""
        model = self.model
        package_uri = model.upload_trainer_dist()
        params = {
            "project_id": model.project_id,
            "job_id_prefix": "train_{}".format(self.job_id),
            "training_input": {
                "scaleTier": model.scale_tier,
                "packageUris": [package_uri],
                "pythonModule": "trainer.task",
                "args": [
                    "--model_dir", model.get_model_dir(),
                ],
                "jobDir": model.get_job_dir(),
                "region": model.region,
                "runtimeVersion": model.runtime_version,
                "pythonVersion": model.python_version,
            },
        }
        return json.dumps(params, indent=4)

    def _get_deploy_params(self):
        """Returns parameters for deploying on CAIP."""
        model = self.model
        params = {
            "project_id": model.project_id,
            "model_id": "{}_kfp".format(model.model["name"]),
            "runtime_version": model.runtime_version,
            "python_version": model.python_version,
        }

        if model.framework != "tensorflow":
            params["model_uri"] = model.get_model_dir()
        return json.dumps(params, indent=4)

    def generate_pipeline(self):
        """Creates the files to compile a pipeline."""
        loader = jinja.PackageLoader("ai_pipeline", "templates")
        env = jinja.Environment(loader=loader, trim_blocks=True,
                                lstrip_blocks="True")
        components, relations = self.to_graph()

        pipeline_template = env.get_template("kfp_pipeline.py")
        pipeline_file = pipeline_template.render(
            train_params=self._get_train_params(),
            # TODO(humichael): TF may have an export dir unlike sklearn.
            model_dir=self.model.get_model_dir(),
            deploy_params=self._get_deploy_params(),
            components=components,
            relations=relations,
        )
        with open("orchestration/pipeline.py", "w+") as f:
            f.write(pipeline_file)
