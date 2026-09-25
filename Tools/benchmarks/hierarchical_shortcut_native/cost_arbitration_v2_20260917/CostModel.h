#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <string>

namespace CostArbitration {
struct Config {
    double graphSetup=.25, postingSetup=.25, member=.05, distance=1, predicate=.2, signature=.05;
    double weight=8, graphNovelty=.5, postingNovelty=.5, discoveryNovelty=.75;
    double predicatePrior=.25, competitivePrior=.25;
    void Validate() const {
        for (double value : {graphSetup, postingSetup, member, distance, predicate, signature, weight})
            if (!std::isfinite(value) || value <= 0) throw std::runtime_error("Invalid arbitration cost/weight");
        for (double value : {graphNovelty, postingNovelty, discoveryNovelty, predicatePrior, competitivePrior})
            if (!std::isfinite(value) || value < 0 || value > 1)
                throw std::runtime_error("Invalid arbitration probability");
    }
};
struct Work {
    std::uint64_t members=0, fresh=0, predicateChecks=0, predicatePasses=0, useful=0;
    Work& operator+=(const Work& w) {
        members+=w.members; fresh+=w.fresh; predicateChecks+=w.predicateChecks;
        predicatePasses+=w.predicatePasses; useful+=w.useful; return *this;
    }
};
struct Prediction {
    double cost=0, gain=0, novelty=0, usefulGivenFresh=0, length=0;
    double Score() const { return gain > 0 ? cost/gain : (std::numeric_limits<double>::infinity)(); }
};
struct Probability { double novelty, useful; };
inline Probability Probabilities(const Config& c, const Work& history, double noveltyPrior) {
    const double novelty=(history.fresh+c.weight*noveltyPrior)/(history.members+c.weight);
    const double useful=(history.useful+c.weight*c.predicatePrior*c.competitivePrior)/(history.fresh+c.weight);
    return {novelty,useful};
}
inline Prediction Predict(const Config& c, Probability p,
                          double length, double remaining, double setup) {
    return {setup+length*(c.member+p.novelty*(c.distance+c.predicate)),
            (std::min)(remaining, length*p.novelty*p.useful),p.novelty,p.useful,length};
}
inline Prediction Predict(const Config& c, const Work& history, double noveltyPrior,
                          double length, double remaining, double setup) {
    return Predict(c,Probabilities(c,history,noveltyPrior),length,remaining,setup);
}
struct Decision {
    int head=-1, level=0, row=-1, chosen=0, checkedBefore=0, checkedAfter=0;
    double remaining=0;
    Prediction graph, posting;
    Work previousGraph, realized;
    std::uint64_t descriptorMembers=0, representativeDistances=0;
    int childRow=-1;
    int remainingChecks=0;
    Prediction tailPosting;
    int source=0;
    double selectedDistance=0, postingSetup=0, tailSetup=0, tailDistance=0;
    std::uint64_t retainedCount=0, retainedChosen=0;
};
}
