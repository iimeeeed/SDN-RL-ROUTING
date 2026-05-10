#!/usr/bin/env bash

echo "==== Python ===="
which python
python --version
which pip
pip --version

echo
echo "==== Ryu ===="
which ryu-manager
ryu-manager --version

echo
echo "==== Mininet ===="
mn --version

echo
echo "==== Open vSwitch ===="
ovs-vsctl --version | head -n 1

echo
echo "==== iPerf3 ===="
iperf3 --version | head -n 1

echo
echo "==== Python packages ===="
python - <<'PY'
import eventlet, ryu, networkx, numpy, pandas, scipy, matplotlib, yaml
print("eventlet:", eventlet.__version__)
print("ryu: installed")
print("networkx:", networkx.__version__)
print("numpy:", numpy.__version__)
print("pandas:", pandas.__version__)
print("scipy:", scipy.__version__)
print("matplotlib:", matplotlib.__version__)
print("pyyaml:", yaml.__version__)
PY
