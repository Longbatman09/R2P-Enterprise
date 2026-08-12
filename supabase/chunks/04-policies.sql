$$;
DROP POLICY IF EXISTS "schools_service_role_all" ON public.schools;
CREATE POLICY "schools_service_role_all"
  ON public.schools FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "schools_owner_all" ON public.schools;
CREATE POLICY "schools_owner_all"
  ON public.schools FOR ALL
  USING (id = public.current_user_school_id());
DROP POLICY IF EXISTS "students_service_role_all" ON public.students;
CREATE POLICY "students_service_role_all"
  ON public.students FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "students_school_all" ON public.students;
CREATE POLICY "students_school_all"
  ON public.students FOR ALL
  USING (school_id = public.current_user_school_id());
DROP POLICY IF EXISTS "school_keys_service_role_all" ON public.school_api_keys;
CREATE POLICY "school_keys_service_role_all"
  ON public.school_api_keys FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "school_keys_school_all" ON public.school_api_keys;
CREATE POLICY "school_keys_school_all"
  ON public.school_api_keys FOR ALL
  USING (school_id = public.current_user_school_id());
DROP POLICY IF EXISTS "invoices_service_role_all" ON public.invoices;
CREATE POLICY "invoices_service_role_all"
  ON public.invoices FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "invoices_school_select" ON public.invoices;
CREATE POLICY "invoices_school_select"
  ON public.invoices FOR SELECT
  USING (school_id = public.current_user_school_id());
DROP POLICY IF EXISTS "report_logs_service_role_all" ON public.report_logs;
CREATE POLICY "report_logs_service_role_all"
  ON public.report_logs FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "report_logs_school_all" ON public.report_logs;
CREATE POLICY "report_logs_school_all"
  ON public.report_logs FOR ALL
  USING (school_id = public.current_user_school_id());
DROP POLICY IF EXISTS "profiles_service_role_all" ON public.profiles;
CREATE POLICY "profiles_service_role_all"
  ON public.profiles FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "profiles_self_all" ON public.profiles;
CREATE POLICY "profiles_self_all"
  ON public.profiles FOR ALL
  USING (id = auth.uid());
DROP POLICY IF EXISTS "user_keys_service_role_all" ON public.user_api_keys;
CREATE POLICY "user_keys_service_role_all"
  ON public.user_api_keys FOR ALL
  USING (auth.role() = 'service_role');
DROP POLICY IF EXISTS "user_keys_owner_all" ON public.user_api_keys;
CREATE POLICY "user_keys_owner_all"
  ON public.user_api_keys FOR ALL
  USING (auth.uid() = user_id);
