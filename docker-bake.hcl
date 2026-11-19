# Image build definition shared by CI and local builds.
#
#   docker buildx bake                 # build mbari/lcm-websocket-server:local
#   docker/smoke_test.sh mbari/lcm-websocket-server:local
#
# In CI, docker/metadata-action overrides the tags and labels of the
# "docker-metadata-action" target.

target "docker-metadata-action" {
  tags = ["mbari/lcm-websocket-server:local"]
}

target "image" {
  inherits   = ["docker-metadata-action"]
  context    = "."
  dockerfile = "docker/Dockerfile"
  platforms  = ["linux/amd64"]
}

group "default" {
  targets = ["image"]
}
