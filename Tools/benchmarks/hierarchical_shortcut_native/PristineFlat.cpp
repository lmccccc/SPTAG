// Validation control: the unmodified pinned archive, using the same native IO/result helpers.
#define main H2SweepUnusedMain
#include "../H2SweepBench.cpp"
#undef main

int main(int argc, char** argv) {
    try {
        Check(argc==3 && std::string(argv[1])=="--config","Usage: pristineflatbench --config absolute.ini");
        auto c = ReadConfig(fs::absolute(argv[2]).string());
        auto heads = ReadVectors(c.heads,true,c), queries = ReadVectors(c.queries,false,c);
        auto flat = Load(c.flat,128);
        Check(flat->GetNumSamples() == heads->Count(),"H1 shape mismatch");
        for (int i=0;i<heads->Count();++i)
            Check(std::memcmp(flat->GetSample(i),heads->GetVector(i),512) == 0,"H1 byte mismatch");
        heads.reset();
        MaxCheck(*flat,c.flatCheck);
        Check(!fs::exists(c.output) && fs::create_directory(c.output),"Output exists");
        fs::copy_file(fs::absolute(argv[2]),fs::path(c.output)/"input.ini");
        auto search = [&](int q) {return Search(*flat,static_cast<const float*>(queries->GetVector(q)),c.k);};
        std::vector<Results> results;
        for(int q=0;q<c.nq;++q) results.push_back(search(q));
        for(int w=0;w<c.warmup;++w) search(w%c.nq);
        std::vector<double> times(c.nq,0);
        for(int r=0;r<c.repeats;++r) for(int q=0;q<c.nq;++q) {
            auto start = Clock::now(); auto result = search(q); auto stop = Clock::now();
            times[q] += std::chrono::duration<double,std::micro>(stop-start).count()/c.repeats;
            Check(result == results[q],"Pristine repetition parity failed");
        }
        std::ofstream rows(fs::path(c.output)/"queries.jsonl"), summary(fs::path(c.output)/"summary.json");
        rows << std::setprecision(12); summary << std::setprecision(12);
        for(int q=0;q<c.nq;++q) {
            rows << "{\"query\":" << q << ",\"ordinary_us\":" << times[q] << ",\"ids\":";
            IDs(rows,results[q]); rows << ",\"distances\":[";
            for(size_t j=0;j<results[q].size();++j) {
                if(j) rows << ','; rows << results[q][j].first;
            }
            rows << "]}\n";
        }
        summary << "{\"queries\":" << c.nq; Stats(summary,"ordinary_us",times); summary << "}\n";
        Check(rows.good() && summary.good(),"Output failed");
        std::cout << "PRISTINE_FLAT us=" << Mean(times) << '\n';
        return 0;
    } catch(const std::exception& error) {
        std::cerr << "PRISTINE_ERROR " << error.what() << '\n'; return 1;
    }
}
