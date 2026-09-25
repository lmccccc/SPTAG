                const auto& n = o.native;
                calls += n.headDistances + n.routingDistances + n.childDistances + n.parentDistances;
                adjacency += n.queueOffers;
                shortcutCalls += n.childDistances;
                dump << "{\"query\":" << i
                     << ",\"work_source\":\"untimed_native_capture\""
                     << ",\"native_raw_distance_function\":" << n.originalDistanceFunction
                     << ",\"native_head_distances\":" << n.headDistances
                     << ",\"native_routing_distances\":" << n.routingDistances
                     << ",\"native_child_distances\":" << n.childDistances
                     << ",\"supplier_parent_distances\":" << n.parentDistances
                     << ",\"supplier_calls\":" << n.calls
                     << ",\"supplier_returns\":" << n.returns
                     << ",\"supplier_members\":" << n.members
                     << ",\"supplier_h2_rows\":" << n.h2Rows
                     << ",\"supplier_h3_rows\":" << n.h3Rows
                     << ",\"signature_checks\":" << n.signatureChecks
                     << ",\"signature_rejects\":" << n.signatureRejects
                     << ",\"native_checked\":" << n.checked
                     << ",\"queue_offers\":" << n.queueOffers
                     << ",\"queue_accepted\":" << n.queueAccepted
                     << ",\"queue_rejected\":" << n.queueRejected
                     << ",\"degree_frames\":[";
                for (size_t j = 0; j < n.frames.size(); ++j) {
                    if (j) dump << ",";
                    const auto& a = n.frames[j];
                    dump << "{\"head\":" << a.head << ",\"d\":" << a.physical
                         << ",\"e\":" << a.eligible << ",\"required\":" << a.required
                         << ",\"startup\":" << a.startup << ",\"calls\":" << a.calls
                         << ",\"checked_before\":" << a.before << ",\"checked_after\":" << a.after
                         << ",\"ordinary\":[";
                    for (size_t k = 0; k < a.ordinary.size(); ++k) {
                        if (k) dump << ",";
                        dump << "[" << a.ordinary[k].first << "," << a.ordinary[k].second << "]";
                    }
                    dump << "],\"supplied\":[";
                    for (size_t k = 0; k < a.supplied.size(); ++k) {
                        if (k) dump << ",";
                        dump << a.supplied[k];
                    }
                    dump << "]}";
                }
                dump << "],\"rows\":[";
                for (size_t j = 0; j < n.rows.size(); ++j) {
                    if (j) dump << ",";
                    const auto& r = n.rows[j];
                    dump << "[" << r.level << "," << r.id << "," << r.size << "," << r.consumed << "]";
                }
                dump << "],\"rejected_rows\":[";
                for (size_t j = 0; j < n.rejectedRows.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << n.rejectedRows[j].first << "," << n.rejectedRows[j].second << "]";
                }
                dump << "],\"evaluated_h1\":[";
                for (size_t j = 0; j < n.evaluated.size(); ++j) {
                    if (j) dump << ",";
                    dump << "[" << n.evaluated[j].first << "," << std::setprecision(9)
                         << n.evaluated[j].second << "]";
                }
                dump << "],\"own_ids\":[";
                for (size_t j = 0; j < o.ownIDs.size(); ++j) {
                    if (j) dump << ",";
                    dump << o.ownIDs[j];
                }
