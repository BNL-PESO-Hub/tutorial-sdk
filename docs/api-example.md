# Common Workflow

```python
from tutorial_sdk import TutorialProject

project = TutorialProject.load("tutorial.yml")

report = project.validate(strict=True)
if not report.passed:
    print(report.render_json())

# ensure Docker is running
build_result = project.build(image="my-org/my-tutorial:v1.0")
# build includes generating artifacts first,
# which you can run manually as following to debug or customize:
#
#   from tutorial_sdk.builder import LocalBuilder
#
#   builder = LocalBuilder(project.spec, project.root)
#   builder.write_dockerfile()
#   builder.write_manifest()
#   builder.write_devcontainer()
#   builder.build(image="my-org/my-tutorial:v1.0")
print(build_result.image, build_result.status)

# run the tutorial container
project.run(image="my-org/my-tutorial:v1.0")
```
