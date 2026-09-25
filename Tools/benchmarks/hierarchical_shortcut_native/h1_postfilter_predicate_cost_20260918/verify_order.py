"""Verify ordered native events, not merely final IDs or predicate totals."""
import json
import numpy as np
from prepare import OUTPUT,sha,write
from order_trace import STAGES

def events(scenario,label):
    return np.fromfile(OUTPUT/f"order_{scenario}_{label}/order.i32","<i4").reshape(-1,6)

def selected(rows,kind):
    return rows[rows[:,2]==ord(kind)]

def means(rows,count):
    return {stage:float(v/count) for stage,v in zip(STAGES,np.bincount(rows[:,1],minlength=5))}

def main():
    reports=[]
    for scenario,count in (("broad_tag",1000),("extreme_tag",32)):
        clean=events(scenario,"clean")
        original=events(scenario,"original")
        graph=events(scenario,"graph")
        bit=events(scenario,"match")
        for suffix in ("head.i32","head.f32"):
            reference=sha(OUTPUT/f"order_{scenario}_clean"/suffix)
            for label in ("original","graph","match"):
                assert sha(OUTPUT/f"order_{scenario}_{label}"/suffix)==reference,(scenario,label,suffix)
        # Untouched authenticated library is the independent callback-order authority.
        assert np.array_equal(clean[:,[0,3,4]],selected(original,"E")[:,[0,3,4]])
        assert np.array_equal(original,graph),(scenario,"native complete event order differs")
        for kind in "HVDAP":
            a=selected(original,kind)
            b=selected(bit,kind).copy()
            if kind=="P": b[:,5]&=1
            assert np.array_equal(a,b),(scenario,kind,"bit native helper inputs/decisions/trajectory differ")
        p=selected(bit,"P")
        e=selected(bit,"E")
        init=e[e[:,5]==1]
        remaining=e[e[:,5]==0]
        native=selected(original,"E")
        reused=p[(p[:,5]&2)!=0]
        assert len(native)==len(p)==len(reused)+len(remaining)
        assert np.all(reused[:,1]==2),"Unexpected bit reuse outside existing ordinary local value"
        totals={"initializations_without_native_result_predicate":0,
                "remaining_result_callbacks_already_initialized":0,
                "remaining_result_callbacks_not_initialized":0,
                "ordinary_initializations":0,"tree_initializations":0}
        for q in range(count):
            def query(rows):
                return rows[np.searchsorted(rows[:,0],q):np.searchsorted(rows[:,0],q+1)]
            i=query(init);r=query(remaining);n=query(native);reuse=query(reused)
            assert len(np.unique(i[:,3]))==len(i),"Repeated visited initialization"
            ids=set(i[:,3]);nativeids=set(n[:,3])
            totals["initializations_without_native_result_predicate"]+=len(ids-nativeids)
            totals["remaining_result_callbacks_already_initialized"]+=sum(x in ids for x in r[:,3])
            totals["remaining_result_callbacks_not_initialized"]+=sum(x not in ids for x in r[:,3])
            totals["ordinary_initializations"]+=int(np.count_nonzero(i[:,1]==2))
            totals["tree_initializations"]+=int(np.count_nonzero(i[:,1]<2))
            initialized={int(row[3]):int(row[4]) for row in i}
            assert all(initialized[int(row[3])]==int(row[4]) for row in reuse)
        extra=len(e)-len(native)
        assert extra==totals["initializations_without_native_result_predicate"]+totals[
            "remaining_result_callbacks_already_initialized"]
        greater=int(np.count_nonzero(selected(original,"P")[:,5]&1))
        report={"scenario":scenario,"queries":count,
            "unmodified_original_callback_sequence_exact":True,
            "original_and_graph_all_events_exact":True,
            "bit_native_helper_inputs_values_admission_visited_distance_exact":True,
            "distance_greater_than_output_bound_but_predicate_still_called_per_query":greater/count,
            "nonnative_distance_shortcut_returns":0,
            "native_result_predicate_calls_per_query":len(native)/count,
            "bit_total_actual_predicate_calls_per_query":len(e)/count,
            "bit_first_initializations_per_query":len(init)/count,
            "bit_remaining_result_callbacks_per_query":len(remaining)/count,
            "bit_local_value_admission_reuses_per_query":len(reused)/count,
            "extra_actual_predicate_calls_per_query":extra/count,
            "native_callbacks_by_stage":means(native,count),
            "bit_initializations_by_stage":means(init,count),
            "bit_remaining_callbacks_by_stage":means(remaining,count),
            "native_helper_invocations_by_stage":means(selected(original,"H"),count),
            "reconciliation_per_query":{k:v/count for k,v in totals.items()},
            "tree_native_direct_admission":"none; seed nodes may later be admitted at popped-head or alias sites",
            "trace_columns":["query","stage","kind","id","value","extra"],
            "trace_kinds":{"E":"actual predicate callback; extra=visited initialization",
                "H":"helper invocation before native guard; value=key, extra=float distance bits",
                "P":"logical predicate evaluation; extra bit0=distance above output bound, bit1=local reuse",
                "A":"native return/admission result","V":"visited probe/result","D":"distance evaluation"},
            "trace_hashes":{label:sha(OUTPUT/f"order_{scenario}_{label}/order.i32")
                            for label in ("clean","original","graph","match")}}
        reports.append(report)
        print(json.dumps(report,indent=2))
    if (OUTPUT/"summary.json").exists():
        summary=json.loads((OUTPUT/"summary.json").read_text())
        proof=reports[0]
        for s in summary:
            expected=proof["native_result_predicate_calls_per_query"] if s["mode"]=="graph" else proof[
                "bit_remaining_result_callbacks_per_query"]
            assert s["h1_admission_mean"]["result_predicate_evaluations"]==expected
            if s["mode"]=="match":
                assert s["match_mean"]["first_match_evaluations"]==proof["bit_first_initializations_per_query"]
                assert s["h1_admission_mean"]["result_predicate_bit_reads"]==proof[
                    "bit_local_value_admission_reuses_per_query"]
            for prefix in s["native_files"]:
                for suffix in ("i32","f32"):
                    assert sha(prefix+".head."+suffix)==sha(OUTPUT/"order_broad_tag_original"/("head."+suffix))
        reports[0]["full_SPANN_capture_heads_and_callback_counts_exact"]=True
    write(OUTPUT/"predicate_order_proof.json",reports)

if __name__=="__main__":main()
