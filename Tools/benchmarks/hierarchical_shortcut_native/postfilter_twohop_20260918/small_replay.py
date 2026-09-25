"""Four bounded32 actual sparse/empty validations; not a performance campaign."""
from prepare import OUTPUT,THRESHOLDS,case,write,protect
from run import environment,invoke,require

def main():
    environment();protect()
    records=[]
    for scenario in ("extreme_tag","unfilter"):
        for mode in ("postfilter_twohop",):
            for t in THRESHOLDS:
                record=invoke(f"small_{scenario}_{case(mode,t)}",scenario,mode,t,small=True)
                records.append(record)
                if scenario=="extreme_tag":
                    require(record["decision_rows"]>0,"No actual sparse decisions")
                    require(record["outer_decision_counts"]["full_two_hop"]>0,"No actual sparse two-hop")
                write(OUTPUT/"small_replay_progress.json",records)
    protect();write(OUTPUT/"small_replay.json",records)

if __name__=="__main__":main()
