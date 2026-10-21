# The devcontainer should use the developer target and run as root with podman
# or docker with user namespaces.
FROM ghcr.io/diamondlightsource/ubuntu-devcontainer:resolute AS developer

# Add any system dependencies for the developer/build environment here
# libgl1 ... libdbus-1-3 let Qt run (offscreen for tests), the xcb libraries
# let it open windows on an X display
RUN apt-get update -y && apt-get install -y --no-install-recommends \
    graphviz \
    libgl1 libegl1 libfontconfig1 libxkbcommon0 libdbus-1-3 \
    libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 \
    libxcb-image0 libxcb-render-util0 libxcb-shape0 libxcb-xkb1 libxcb-randr0 \
    && apt-get dist-clean

# The build stage installs the context into the venv
FROM developer AS build

# Change the working directory to the `app` directory
# and copy in the project
WORKDIR /app
COPY . /app
RUN chmod o+wrX .

# Tell uv sync to install python in a known location so we can copy it out later
ENV UV_PYTHON_INSTALL_DIR=/python

# Sync the project without its dev dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-dev --managed-python

# The runtime stage copies the built venv into a runtime container
FROM ubuntu:resolute AS runtime

# Add apt-get system dependecies for runtime here if needed
# RUN apt-get update -y && apt-get install -y --no-install-recommends \
#     some-library \
#     && apt-get dist-clean

# Copy the python installation from the build stage
COPY --from=build /python /python

# Copy the environment, but not the source code
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH

# change this entrypoint if it is not the same as the repo
ENTRYPOINT ["dls-ibek-yaml-editor"]
CMD ["--version"]
