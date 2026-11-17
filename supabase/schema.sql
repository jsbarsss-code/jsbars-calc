-- 우리집 열쇠: 로그인 사용자의 지도·일정 사본 (한 사람당 한 행)
-- Supabase 대시보드 → SQL Editor에 통째로 붙여 넣고 Run. 여러 번 실행해도 안전하다.
-- 서버에 두는 것: 지도 체크·저축 목표·은행 비교 메모·입주까지 일정. 연봉·현금 입력값은 올리지 않는다(브라우저가 빼고 보낸다).

create table if not exists public.ujk_sync (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  data       jsonb not null,
  agreed_at  timestamptz not null default now(),          -- 처리방침 동의 시각(첫 저장)
  updated_at timestamptz not null default now(),
  constraint ujk_sync_size check (octet_length(data::text) <= 200000)   -- 한 사람당 200KB 상한(무료 용량 남용 방지)
);

-- 저장 시각은 서버 시계로만 (브라우저 시계를 믿지 않는다)
create or replace function public.ujk_touch() returns trigger
language plpgsql set search_path = '' as $$
begin
  new.updated_at := now();
  if tg_op = 'UPDATE' then new.agreed_at := old.agreed_at; end if;
  return new;
end $$;
drop trigger if exists ujk_sync_touch on public.ujk_sync;
create trigger ujk_sync_touch before insert or update on public.ujk_sync
  for each row execute function public.ujk_touch();

-- 본인 행만 읽고 쓰기 (RLS). 로그인 안 한 사람(anon)은 표 자체에 권한이 없다.
alter table public.ujk_sync enable row level security;
alter table public.ujk_sync force row level security;
revoke all on public.ujk_sync from anon, public;
grant select, insert, update, delete on public.ujk_sync to authenticated;

drop policy if exists ujk_own_select on public.ujk_sync;
drop policy if exists ujk_own_insert on public.ujk_sync;
drop policy if exists ujk_own_update on public.ujk_sync;
drop policy if exists ujk_own_delete on public.ujk_sync;
create policy ujk_own_select on public.ujk_sync for select to authenticated using ((select auth.uid()) = user_id);
create policy ujk_own_insert on public.ujk_sync for insert to authenticated with check ((select auth.uid()) = user_id);
create policy ujk_own_update on public.ujk_sync for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create policy ujk_own_delete on public.ujk_sync for delete to authenticated using ((select auth.uid()) = user_id);

-- 탈퇴: 로그인한 본인 계정만 즉시 삭제(사본 행은 on delete cascade로 함께 지워진다)
create or replace function public.delete_my_account() returns void
language plpgsql security definer set search_path = '' as $$
begin
  if auth.uid() is null then raise exception 'not signed in'; end if;
  delete from auth.users where id = auth.uid();
end $$;
revoke all on function public.delete_my_account() from public, anon;
grant execute on function public.delete_my_account() to authenticated;
revoke all on function public.ujk_touch() from public, anon, authenticated;

-- ---- 자체 시험: 가짜 계정 A·B로 서로의 사본이 안 보이는지 확인하고 흔적 없이 지운다 ----
do $$
declare a uuid := gen_random_uuid(); b uuid := gen_random_uuid(); n int;
begin
  insert into auth.users (id, aud, role, email) values
    (a, 'authenticated', 'authenticated', 'rls-test-a-' || a || '@example.invalid'),
    (b, 'authenticated', 'authenticated', 'rls-test-b-' || b || '@example.invalid');

  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  insert into public.ujk_sync (user_id, data) values (a, '{"t":"A"}');
  begin
    insert into public.ujk_sync (user_id, data) values (b, '{"t":"남의 칸"}');
    raise exception 'RLS 실패: A가 B 이름으로 저장했어요';
  exception when insufficient_privilege then null;   -- 막혀야 정상
  end;
  execute 'reset role';

  perform set_config('request.jwt.claims', json_build_object('sub', b, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  select count(*) into n from public.ujk_sync;
  if n <> 0 then raise exception 'RLS 실패: B에게 A의 사본이 % 행 보여요', n; end if;
  update public.ujk_sync set data = '{"t":"덮어쓰기"}' where user_id = a;
  get diagnostics n = row_count;
  if n <> 0 then raise exception 'RLS 실패: B가 A의 사본을 고쳤어요'; end if;
  delete from public.ujk_sync where user_id = a;
  get diagnostics n = row_count;
  if n <> 0 then raise exception 'RLS 실패: B가 A의 사본을 지웠어요'; end if;
  begin
    insert into public.ujk_sync (user_id, data) values (b, to_jsonb(repeat('x', 210000)));
    raise exception 'RLS 실패: 200KB 상한이 안 걸려요';
  exception when check_violation then null;
  end;
  execute 'reset role';

  perform set_config('request.jwt.claims', json_build_object('sub', a, 'role', 'authenticated')::text, true);
  execute 'set local role authenticated';
  select count(*) into n from public.ujk_sync;
  if n <> 1 then raise exception 'RLS 실패: A가 자기 사본을 못 봐요(% 행)', n; end if;
  perform public.delete_my_account();   -- 탈퇴: A 계정과 사본이 함께 지워져야 한다
  execute 'reset role';

  select count(*) into n from public.ujk_sync where user_id = a;
  if n <> 0 then raise exception '탈퇴 실패: A의 사본이 남았어요'; end if;
  select count(*) into n from auth.users where id = a;
  if n <> 0 then raise exception '탈퇴 실패: A 계정이 남았어요'; end if;

  delete from auth.users where id in (a, b);
end $$;

select '시험 통과: 다른 사람 사본 읽기·고치기·지우기 차단, 200KB 상한, 탈퇴 즉시 삭제 확인' as result;
