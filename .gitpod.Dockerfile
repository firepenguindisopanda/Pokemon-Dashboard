FROM gitpod/workspace-full
                    
USER root
RUN sudo apt-get update \
 && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
   libgtk2.0-0 \
   libgtk-3-0 \
   libnotify-dev \
   libgconf-2-4 \
   libnss3 \
   libxss1 \
   libasound2 \
   libxtst6 \
   xauth \
   xvfb \
 && sudo rm -rf /var/lib/apt/lists/*

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      libgbm1 \
      libgtk-3-0 \
      libnotify4 \
      libgconf-2-4 \
      libnss3 \
      libxss1 \
      libasound2 \
      libxtst6 \
 && rm -rf /var/lib/apt/lists/*

USER gitpod
RUN npm i -g mocha
RUN npm i -g mocha puppeteer