#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# install_tools.sh -- build the open-source EDA toolchain this project needs.
#
# Tested on Ubuntu 22.04 and 24.04 (including WSL2). Run with sudo, or as root.
#
#   sudo ./setup/install_tools.sh
#
# Installs:
#   yosys        RTL synthesis, JSON netlist export, formal (miter + sat)
#   OpenSTA      static timing analysis (built from source with CUDD)
#   verilator    fast lint
#   iverilog     simulation for the RTL self-checks
#   Nangate45    open standard-cell library, three corners (setup/hold/typ)
#   SKY130 HD    open foundry standard-cell library, typical corner
#
# Everything lands under /opt/pdk and /usr/local/bin.
# ---------------------------------------------------------------------------
set -euo pipefail

PDK_ROOT="${GENRTL_PDK_ROOT:-/opt/pdk}"
JOBS="$(nproc || echo 2)"
export DEBIAN_FRONTEND=noninteractive

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
say "1/5  build dependencies"
apt-get update -y
apt-get install -y --no-install-recommends \
    build-essential clang cmake git curl wget unzip xz-utils \
    tcl-dev tcl8.6 tcllib swig bison flex libfl-dev libreadline-dev gawk \
    libffi-dev pkg-config zlib1g-dev python3-dev python3-pip graphviz \
    libeigen3-dev libboost-all-dev libspdlog-dev \
    automake autoconf libtool m4 \
    yosys iverilog verilator

# ---------------------------------------------------------------------------
say "2/5  CUDD (required by OpenSTA)"
if [ ! -f /opt/cudd-install/include/cudd.h ]; then
    rm -rf /opt/cudd
    git clone --depth 1 https://github.com/The-OpenROAD-Project/cudd.git /opt/cudd
    cd /opt/cudd
    ./configure --prefix=/opt/cudd-install --enable-shared
    # The tarball ships autotools outputs older than configure.ac, which makes
    # make try to re-run aclocal-1.14. Touching them in order avoids that.
    touch aclocal.m4 configure config.h.in Makefile.in ./*/Makefile.in 2>/dev/null || true
    sleep 1 && touch config.status
    make -j"${JOBS}"
    make install
    echo /opt/cudd-install/lib > /etc/ld.so.conf.d/cudd.conf
    ldconfig
fi

# ---------------------------------------------------------------------------
say "3/5  OpenSTA"
if ! command -v sta >/dev/null 2>&1; then
    rm -rf /opt/OpenSTA
    git clone --depth 1 https://github.com/parallaxsw/OpenSTA.git /opt/OpenSTA
    mkdir -p /opt/OpenSTA/build && cd /opt/OpenSTA/build
    cmake .. -DCMAKE_BUILD_TYPE=Release -DCUDD_DIR=/opt/cudd-install
    make -j"${JOBS}"
    make install
    ldconfig
fi

# ---------------------------------------------------------------------------
say "4/5  standard-cell libraries"
mkdir -p "${PDK_ROOT}/nangate45" "${PDK_ROOT}/sky130hd"

NG=https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD/master/test/Nangate45
for f in Nangate45_typ.lib Nangate45_fast.lib Nangate45_slow.lib; do
    [ -s "${PDK_ROOT}/nangate45/$f" ] || curl -sSL -o "${PDK_ROOT}/nangate45/$f" "$NG/$f"
done

SKY=https://raw.githubusercontent.com/The-OpenROAD-Project/OpenROAD-flow-scripts/master/flow/platforms/sky130hd/lib
[ -s "${PDK_ROOT}/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib" ] || \
    curl -sSL -o "${PDK_ROOT}/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib" \
        "$SKY/sky130_fd_sc_hd__tt_025C_1v80.lib"

# Optional fast/slow SKY130 corners. Without them, hold analysis on sky130hd
# falls back to the typical corner and `genrtl check` says so.
SKYPDK=https://raw.githubusercontent.com/google/skywater-pdk-libs-sky130_fd_sc_hd/main/timing
for f in sky130_fd_sc_hd__ff_n40C_1v95.lib sky130_fd_sc_hd__ss_n40C_1v40.lib; do
    [ -s "${PDK_ROOT}/sky130hd/$f" ] || \
        curl -sSL -o "${PDK_ROOT}/sky130hd/$f" "$SKYPDK/$f" || true
    # a 404 leaves a tiny file behind; drop it so the fallback kicks in
    [ -s "${PDK_ROOT}/sky130hd/$f" ] && \
        [ "$(stat -c%s "${PDK_ROOT}/sky130hd/$f")" -lt 100000 ] && \
        rm -f "${PDK_ROOT}/sky130hd/$f"
done

# ---------------------------------------------------------------------------
say "5/5  python packages"
pip3 install --break-system-packages -q -r "$(dirname "$0")/../requirements.txt" || \
    pip3 install -q -r "$(dirname "$0")/../requirements.txt"

say "done -- verifying"
yosys -V
sta -version
verilator --version
echo
echo "Now run:  python3 -m genrtl check"
