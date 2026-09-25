"""Optional fixed sparse pair, gated only by functional runtime and after Broad."""
import json
import statistics
from prepare import HERE,OUTPUT,TOOL,case,sha,write,protect
from run import process,validate,require

def main():
    require(not (OUTPUT/"milestone_manifest.json").exists(),"Sealed milestone")
    broad=json.loads((OUTPUT/"operations.json").read_text())
    require(len(broad["records"])==6,"Broad restoration comparison must finish first")
    small=json.loads((OUTPUT/"small_replay.json").read_text())
    runtime=max(r["native"]["mean_latency_ms"] for r in small["records"] if r["scenario"]=="extreme_tag")
    require(runtime<10,"Sparse functional replay exceeds bounded runtime")
    folder=OUTPUT/"sparse_configs";folder.mkdir()
    configs={}
    for mode in ("graph","posting"):
        text=(HERE/"diagnostic_configs"/f"extreme_tag_{case(mode)}.ini").read_text()
        text=text.replace("Warmup=32","Warmup=1000").replace("MaxQueries=32","MaxQueries=1000")
        text=text.replace("DiagnosticOnly=true\n","")
        cfg=folder/(mode+".ini");cfg.write_text(text);configs[mode]=cfg
    write(OUTPUT/"sparse_plan.json",{"after_broad":True,"preflight_max_ms":runtime,
        "rule":"fixed optional pair admitted only by <10ms runtime; no recall-based selection",
        "order":["graph","posting","posting","graph"],"ratio":.01,"warmup":1000,"measure":1000})
    records=[]
    for rep,modes in ((1,("graph","posting")),(2,("posting","graph"))):
        for mode in modes:
            label=f"extreme_tag_{case(mode)}_r{rep}"
            directory=OUTPUT/label;directory.mkdir()
            seconds=process(["numactl","--cpunodebind=2","--membind=2",str(TOOL/"harness/postfilter-bench"),
                "--config",str(configs[mode])],directory,max_seconds=180)
            write(directory/"process_seconds.json",{"seconds":seconds})
            record=validate(directory,"extreme_tag",mode,1000,seconds)
            record.update(config=str(configs[mode]),config_sha256=sha(configs[mode]))
            write(directory/"record.json",record);records.append(record)
            write(OUTPUT/"sparse_operations.json",records)
    summary=[]
    for mode in ("graph","posting"):
        pair=[r for r in records if r["mode"]==mode]
        require(pair[0]["payloads"]==pair[1]["payloads"] and pair[0]["trace_hashes"]==pair[1]["trace_hashes"],
                "Sparse pair nondeterministic")
        ms=[r["native"]["mean_latency_ms"] for r in pair]
        summary.append({"scenario":"extreme_tag","mode":mode,"ordinary_ms_runs":ms,
            "ordinary_ms":statistics.mean(ms),"qps_at_mean_ms":1000/statistics.mean(ms),
            "recall_at_10":pair[0]["native"]["recall"],
            **{key:pair[0][key] for key in ("native_mean","match_mean","action_mean","ssd_mean")}})
    write(OUTPUT/"sparse_summary.json",summary);protect()

if __name__=="__main__":main()
