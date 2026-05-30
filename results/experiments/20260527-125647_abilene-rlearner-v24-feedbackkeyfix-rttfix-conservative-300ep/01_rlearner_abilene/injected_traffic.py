import os, sys, traceback
sys.path.insert(0, '/home/johndoe/Desktop/sdn-rl-routing')
os.environ['TRAFFIC_PAIRS'] = 'h1-h5,h2-h4,h1-h4'
os.environ['TRAFFIC_PROTOCOLS'] = 'both'
os.environ['TRAFFIC_PAIR_COUNT'] = '2'
os.environ['TRAFFIC_PAIR_MODE'] = 'ends'
os.environ['TRAFFIC_FLOWS_PER_PAIR'] = '1'
os.environ['TRAFFIC_EPISODES'] = '300'
os.environ['TRAFFIC_DURATION'] = '30'
os.environ['TRAFFIC_INTERVAL'] = '1'
os.environ['TRAFFIC_LINK_BW_Mbps'] = '100'
os.environ['TRAFFIC_STAGGER_MIN'] = '1.0'
os.environ['TRAFFIC_STAGGER_MAX'] = '3.0'
os.environ['TRAFFIC_OUTPUT'] = '/home/johndoe/Desktop/sdn-rl-routing/results/experiments/20260527-125647_abilene-rlearner-v24-feedbackkeyfix-rttfix-conservative-300ep/01_rlearner_abilene/traffic.csv'
os.environ['TRAFFIC_DONE'] = '/home/johndoe/Desktop/sdn-rl-routing/results/experiments/20260527-125647_abilene-rlearner-v24-feedbackkeyfix-rttfix-conservative-300ep/01_rlearner_abilene/traffic.csv.done'
os.environ['TRAFFIC_PING'] = '1'
os.environ['TRAFFIC_VERBOSE'] = '1'
os.environ['TRAFFIC_FEEDBACK_HOST'] = '127.0.0.1'
os.environ['TRAFFIC_FEEDBACK_GRACE'] = '0.2'
os.environ['TRAFFIC_CONCURRENT'] = '0'
os.environ['TRAFFIC_SEED'] = '1'
os.environ['TRAFFIC_FEEDBACK_PORT'] = '9999'
try:
	__import__('traffic.generate_traffic', fromlist=['run_from_env']).run_from_env(net)
except BaseException:
	with open('/home/johndoe/Desktop/sdn-rl-routing/results/experiments/20260527-125647_abilene-rlearner-v24-feedbackkeyfix-rttfix-conservative-300ep/01_rlearner_abilene/traffic.csv.partial', 'w') as handle:
		handle.write('complete=0\n')
		handle.write('error=injected traffic exception\n')
		traceback.print_exc(file=handle)
	raise
