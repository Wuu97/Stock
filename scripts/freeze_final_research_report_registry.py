import json, hashlib
from pathlib import Path

OUT=Path('data/ridge_history_evidence/full_five_results')
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def main():
    files={
      'v1_initial_research_audit':'competition_v4_ridge_3y_final_research_audit.json',
      'v2_corrected_methodology_audit':'competition_v4_ridge_3y_corrected_final_research_audit_v2.json',
      'v3_financial_attachment':'competition_v4_ridge_3y_profit_bridge_and_concentration_v3.json',
    }
    hashes={k:sha(OUT/v) for k,v in files.items()}
    registry={
      'status':'FINAL_RESEARCH_REPORT_REGISTRY',
      'version_relationship':{
        'v1_initial_research_audit':'Retained immutable historical audit; superseded for corrected segment, drawdown and turnover methodology.',
        'v2_corrected_methodology_audit':'Primary final research audit for methodology and results interpretation.',
        'v3_financial_attachment':'Financial appendix to V2; reconciles the profit bridge, fee treatment and concentration denominator. It does not replace V1 or V2.',
      },
      'files':[{ 'role':k,'path':str(OUT/v),'sha256':hashes[k]} for k,v in files.items()],
      'research_limitations':[
        'Full-period IC, Rank IC and Q5-Q1 HAC confidence intervals cross zero. The evidence does not establish stable positive Ridge predictive ability.',
        'Previously observed historical periods, including the prior 223-day study overlap, are not new independent validation samples. Repeated parameter adjustment on them cannot remedy that limitation.'
      ],
      'completion_scope':'The 727-day continuous five-strategy replay and its financial audit are complete. No retraining, replay or historical backfill is implied by this registry.'
    }
    p=OUT/'competition_v4_ridge_3y_final_report_registry_v1.json';p.write_text(json.dumps(registry,indent=2)+'\n'); print(json.dumps({'path':str(p),'sha256':sha(p),'source_hashes':hashes},indent=2))
if __name__=='__main__':main()
