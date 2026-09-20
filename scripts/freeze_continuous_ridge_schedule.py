"""Bind pre-frozen Ridge models to one continuous Competition Profile."""
import argparse, json
from hashlib import sha256
from pathlib import Path
from quant_core.ridge_schedule import SCHEDULE_SCHEMA_VERSION, RidgeModelSchedule

def digest(path): return sha256(Path(path).read_bytes()).hexdigest()
def main():
 p=argparse.ArgumentParser(); p.add_argument('--profile-id',required=True); p.add_argument('--profile-version',required=True); p.add_argument('--profile-hash',required=True); p.add_argument('--dataset',required=True); p.add_argument('--dataset-manifest',required=True); p.add_argument('--source-schedule',action='append',required=True); p.add_argument('--output',required=True); a=p.parse_args()
 output=Path(a.output)
 if output.exists(): raise FileExistsError('continuous schedule already exists')
 models=[]
 for source in a.source_schedule:
  schedule=RidgeModelSchedule.load(Path(source)); raw=json.loads(Path(source).read_text())
  if raw.get('dataset_sha256')!=digest(a.dataset) or raw.get('dataset_manifest_sha256')!=digest(a.dataset_manifest): raise ValueError('source schedule dataset binding mismatch')
  models.extend(raw['models'])
 models.sort(key=lambda item:item['prediction_dates'][0])
 payload={'schema_version':SCHEDULE_SCHEMA_VERSION,'dataset_sha256':digest(a.dataset),'dataset_manifest_sha256':digest(a.dataset_manifest),'competition_profile':{'id':a.profile_id,'version':a.profile_version,'hash':a.profile_hash},'models':models}
 output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
 RidgeModelSchedule.load(output)
 print(json.dumps({'schedule_sha256':digest(output),'models':len(models)}))
if __name__=='__main__': main()
