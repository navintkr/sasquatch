/* Example 5: a macro that is *invoked* (expanded), plus statistics & a model. */
%macro region_rollup(ds=, metric=);
   proc means data=&ds mean sum n;
      class region;
      var &metric;
      output out=&ds._rollup;
   run;
%mend region_rollup;

%region_rollup(ds=work.enriched, metric=revenue);

proc corr data=work.enriched;
   var revenue cost margin;
run;

proc reg data=work.enriched;
   model revenue = cost margin tax;
run;

proc logistic data=work.enriched;
   model churn = revenue margin;
run;
