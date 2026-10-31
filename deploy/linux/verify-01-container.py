"""No-model integration check of the official image's unprivileged TCP path."""
import json
from pathlib import Path
import socket
import subprocess
import time

IMAGE = 'registry-1.docker.io/tjbtech1/airesearcher@sha256:82ac65712eb504f100bff34448004004bb6e16fb6bf1e8f4cf8d37a60b0f8345'
NAME = 'research-stage-e-tcp-20261023-02'
ROOT = Path.home() / 'research-runtime/runs/stage-e-tcp-20261023-02'
ROOT.mkdir(exist_ok=False)
def docker(*args):
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=90, check=True).stdout
result = {'image':IMAGE, 'model_requests':0, 'purpose':'container_infrastructure_only_not_full_researcher_acceptance'}
created = False
try:
    docker('run','-d','--name',NAME,'--label','research.task_id=stage-e-tcp-20261023','--label','research.project_id=integration-demo','--user','1000:1000','--cpus','4','--memory','4g','--memory-swap','4g','--pids-limit','256','--cap-drop','ALL','--security-opt','no-new-privileges','--restart','no','--gpus','all','-p','127.0.0.1:18766:8000','--mount',f'type=bind,src={ROOT},dst=/workplace','--workdir','/workplace','--env','HOME=/tmp','--entrypoint','python',IMAGE,'/app/tcp_server.py','--workplace','/workplace','--port','8000')
    created = True
    for attempt in range(45):
        try:
            with socket.create_connection(('127.0.0.1',18766),timeout=2) as client:
                client.settimeout(30)
                client.sendall(b'python -c "import os,torch; print(os.getuid()); print(torch.ones(2,device=\'cuda\').sum().item())"')
                chunks = []
                while True:
                    data = client.recv(4096)
                    if not data:
                        break
                    chunks.append(data.decode())
                    if any(json.loads(line).get('type') == 'final' for line in ''.join(chunks).splitlines() if line.endswith('}')):
                        break
                result['tcp_response'] = ''.join(chunks)
                final = [json.loads(line) for line in result['tcp_response'].splitlines() if json.loads(line).get('type') == 'final'][-1]
                assert final['status'] == 0 and final['result'].splitlines() == ['1000', '2.0']
                break
        except (ConnectionError,TimeoutError):
            time.sleep(1)
    else:
        raise RuntimeError('TCP server unavailable')
    info=json.loads(docker('inspect',NAME))[0]
    result['container_id']=info['Id']
    result['host_config']={k:info['HostConfig'][k] for k in ['Memory','NanoCpus','PidsLimit','CapDrop','SecurityOpt','PortBindings']}
finally:
    if created:
        info=json.loads(docker('inspect',NAME))[0]
        assert info['Config']['Labels']['research.task_id']=='stage-e-tcp-20261023'
        docker('stop','--time','5',NAME)
        result['stopped']=not json.loads(docker('inspect',NAME))[0]['State']['Running']
    (ROOT/'acceptance.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='tcp_response'}))
