FROM ubuntu:25.10
ENV DEBIAN_FRONTEND=noninteractive
RUN \
  apt-get update -y && \
  apt-get install -y unzip  && \
  apt-get install -y git  && \
  apt-get install -y wget && \
  apt-get install -y apt-transport-https ca-certificates gnupg && \
  apt-get install -y curl && \
  apt-get install -y vim && \
  apt-get install -y jq && \
  apt-get install -y make python3 python3-pip

RUN curl https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
  && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list && \
  apt-get update -y && \
  apt-get install -y google-cloud-cli 

RUN curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
RUN . ~/.bashrc && nvm install --lts
RUN pip install --no-cache-dir grpcio-tools --break-system-packages

COPY . evalbench
WORKDIR evalbench
RUN ln -s /usr/bin/python3 /usr/bin/python
RUN make proto -f ./Makefile
RUN mkdir /tmp_session_files
RUN chown -R 65532:65532 /evalbench /tmp /tmp_session_files

RUN pip install --no-cache-dir -r requirements.txt --break-system-packages
RUN ln -s /root/.nvm/versions/node/v24.14.0/bin/npm /usr/bin/npm
RUN ln -s /root/.nvm/versions/node/v24.14.0/bin/node /usr/bin/node
RUN ln -s /root/.nvm/versions/node/v24.14.0/bin/npx /usr/bin/npx
CMD ["/evalbench/run_service.sh"]
EXPOSE 50051
