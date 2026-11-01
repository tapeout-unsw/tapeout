KV260 offline setup (Ubuntu 22.04 ARM64 / Python 3.10)
======================================================

Place this entire kria_offline folder inside /home/ubuntu/host_app so that:

  /home/ubuntu/host_app/kria_offline/install_offline.py
  /home/ubuntu/host_app/kria_offline/wheels/
  /home/ubuntu/host_app/kria_offline/debs/

The supported command is at the root of host_app:

  cd /home/ubuntu/host_app
  bash run.sh

run.sh verifies the hardware files, calls the offline installer when needed,
performs the safe overlay smoke test and starts the interface. The installer
does not use apt, pip, or the network. It extracts the bundled ARM64 Ubuntu
RISC-V compiler and Python wheels into host_app/toolchain and host_app/vendor.

Then browse to:

  http://<kria-ip>:8000
