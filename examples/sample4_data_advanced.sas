/* Example 4: advanced DATA step -- MERGE, BY/FIRST.-LAST., RETAIN, LAG/DIF. */
data work.joined;
   merge work.sales work.targets;
   by region customer_id;
run;

data work.running;
   set work.joined;
   by region;
   retain cum_revenue 0;
   cum_revenue = cum_revenue + revenue;
   prev_revenue = lag(revenue);
   delta = dif(revenue);
   if first.region then flag_first = 1;
   if last.region then flag_last = 1;
run;
