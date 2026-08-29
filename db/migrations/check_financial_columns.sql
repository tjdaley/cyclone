-- Which of the financial migrations have actually landed.
--
-- Every row should say 'present'. A 'MISSING' row names the migration to run,
-- and tells you exactly which endpoint is failing: a model field with no column
-- behind it is not a degraded feature, it is a 500 on every read of that table
-- (PostgREST rejects the whole row for an unknown column, PGRST204).
select 'financial_accounts.ownership'              as column_name,
       '026' as migration,
       case when exists (select 1 from information_schema.columns
                          where table_name = 'financial_accounts' and column_name = 'ownership')
            then 'present' else 'MISSING — GET /financial-accounts returns 500' end as status
union all
select 'financial_accounts.antecedent_account_id', '026',
       case when exists (select 1 from information_schema.columns
                          where table_name = 'financial_accounts' and column_name = 'antecedent_account_id')
            then 'present' else 'MISSING — GET /financial-accounts returns 500' end
union all
select 'jobs.params', '025',
       case when exists (select 1 from information_schema.columns
                          where table_name = 'jobs' and column_name = 'params')
            then 'present' else 'MISSING — statement upload returns 502' end
union all
select 'jobs.matter_id', '023',
       case when exists (select 1 from information_schema.columns
                          where table_name = 'jobs' and column_name = 'matter_id')
            then 'present' else 'MISSING — statement upload returns 502' end
union all
select 'financial_account_transactions.category_id', '024',
       case when exists (select 1 from information_schema.columns
                          where table_name = 'financial_account_transactions' and column_name = 'category_id')
            then 'present' else 'MISSING — transaction reads return 500' end
union all
select 'financial_account_transactions.bates_number', '024',
       case when exists (select 1 from information_schema.columns
                          where table_name = 'financial_account_transactions' and column_name = 'bates_number')
            then 'present' else 'MISSING — transaction reads return 500' end
union all
select 'transaction_categories (table)', '024',
       case when exists (select 1 from information_schema.tables where table_name = 'transaction_categories')
            then 'present' else 'MISSING — category endpoints return 500' end
union all
select 'transaction_tags (table)', '024',
       case when exists (select 1 from information_schema.tables where table_name = 'transaction_tags')
            then 'present' else 'MISSING — tag endpoints return 500' end
union all
select 'transactions FK is ON UPDATE CASCADE', '026',
       case when exists (
              select 1 from pg_constraint
               where conname = 'financial_account_transactions_parent_fkey' and confupdtype = 'c')
            then 'present' else 'MISSING — account merge will fail' end
order by status desc, column_name;
