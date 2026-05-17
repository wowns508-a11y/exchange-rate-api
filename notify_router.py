from fastapi import APIRouter, HTTPException
from datetime import date, timedelta
import httpx
import os
import logging

router = APIRouter()

# ── 환경변수 (Railway Variables에 등록) ─────────────────────────────────────
SUPABASE_URL      = os.getenv("SUPABASE_URL", "https://aaexsceexmzdufwyxjvu.supabase.co")
SUPABASE_KEY      = os.getenv("SUPABASE_KEY", "")       # service_role key
RESEND_API_KEY    = os.getenv("RESEND_API_KEY", "")     # Resend API key
FROM_EMAIL        = os.getenv("FROM_EMAIL", "재경팀 <noreply@yourdomain.com>")
CRON_SECRET       = os.getenv("CRON_SECRET", "")           
TEAMS_WEBHOOK_URL = os.getenv("TEAMS_WEBHOOK_URL", "")  # ★ 팀즈 파워오토메이트 웹훅 주소


# ── Supabase REST 헬퍼 ────────────────────────────────────────────────────────

def sb_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }


async def get_schedules_by_dday(days: int) -> list[dict]:
    """D-{days}인 미완료 일정 조회"""
    target_date = (date.today() + timedelta(days=days)).isoformat()
    url = (
        f"{SUPABASE_URL}/rest/v1/tax_schedules"
        f"?due_date=eq.{target_date}"
        f"&is_done=eq.false"
        f"&category=neq.공휴일"
        f"&select=*"
    )
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=sb_headers())
        r.raise_for_status()
        return r.json()


async def get_user_info(user_name: str) -> dict | None:
    """public.users 테이블에서 name으로 사번(employee_id)과 이메일 함께 조회"""
    url = (
        f"{SUPABASE_URL}/rest/v1/users"
        f"?name=eq.{user_name}"
        f"&approved=eq.true"
        f"&select=email,employee_id"
        f"&limit=1"
    )
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=sb_headers())
        if r.status_code == 200 and r.json():
            return r.json()[0]
    return None


async def get_manager_emails() -> list[str]:
    """role = manager인 사람들 이메일 조회 → CC 수신자"""
    url = f"{SUPABASE_URL}/rest/v1/users?select=email&role=eq.manager&approved=eq.true"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=sb_headers())
        if r.status_code == 200 and r.json():
            return [row["email"] for row in r.json() if row.get("email")]
    return []


# ── 팀즈 발송 헬퍼 (새로 추가) ──────────────────────────────────────────────────

async def send_teams_notification(employee_id: str, message_text: str) -> bool:
    """파워 오토메이트 트리거 채널에 사번$$$메시지 형태로 웹훅 요청 쏘기"""
    if not TEAMS_WEBHOOK_URL:
        logging.warning("[Teams] TEAMS_WEBHOOK_URL이 설정되지 않아 발송을 스킵합니다.")
        return False
        
    # 선임님과 기를 쓰며 맞췄던 완벽한 수식의 근본 약속 포맷
    payload = {
        "content": f"{employee_id}$$$🚨 D-day 알림 🚨\n{message_text}"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(TEAMS_WEBHOOK_URL, json=payload, timeout=10)
            logging.warning(f"[Teams] status={r.status_code} body={r.text} payload={payload}")
            return r.status_code in [200, 202]
        except Exception as e:
            logging.error(f"[Teams] 발송 중 익셉션 발생: {e}")
            return False


# ── 이메일 발송 (Resend) ───────────────────────────────────────────────────────

async def send_email(to: str, subject: str, html: str, cc: list[str] = []) -> bool:
    payload = {
        "from": FROM_EMAIL,
        "to": [to],
        "subject": subject,
        "html": html,
    }
    if cc:
        payload["cc"] = cc
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        logging.warning(f"[Resend] status={r.status_code} body={r.text} from={FROM_EMAIL} to={to}")
        return r.status_code == 200


def build_email_html(schedules: list[dict], days: int) -> str:
    """이메일 HTML 생성 — Apple 스타일"""
    FONT = "Verdana, Geneva, 'Apple SD Gothic Neo', sans-serif"
    if days == 0:
        urgency_color = "#ef4444"
        urgency_text  = "Today"
    elif days == 1:
        urgency_color = "#ef4444"
        urgency_text  = "D-1"
    elif days <= 7:
        urgency_color = "#f97316"
        urgency_text  = f"D-{days}"
    else:
        urgency_color = "#0066cc"
        urgency_text  = f"D-{days}"

    rows = ""
    for s in schedules:
        due = str(s.get("due_date", ""))[:10]
        rows += f"""
        <tr>
            <td style="padding:14px 0;border-bottom:1px solid #f0f0f0;font-size:15px;color:#1d1d1f;font-weight:600;letter-spacing:-0.374px;font-family:{FONT};">{s.get("title", "")}</td>
            <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#7a7a7a;letter-spacing:-0.224px;font-family:{FONT};">{s.get("category", "")}</td>
            <td style="padding:14px 16px;border-bottom:1px solid #f0f0f0;font-size:13px;color:#7a7a7a;letter-spacing:-0.224px;font-family:{FONT};">{s.get("target_entity", "")}</td>
            <td style="padding:14px 0;border-bottom:1px solid #f0f0f0;font-size:13px;color:{urgency_color};font-weight:600;letter-spacing:-0.224px;text-align:right;font-family:{FONT};">{due}</td>
        </tr>
        """

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f5f5f7;font-family:{FONT};-webkit-font-smoothing:antialiased;">
    <div style="max-width:580px;margin:48px auto;background:#ffffff;border-radius:18px;overflow:hidden;">
        <div style="background:#000000;padding:24px 40px;display:flex;align-items:center;">
            <div>
                <div style="font-size:11px;color:#86868b;letter-spacing:0.05em;text-transform:uppercase;margin-bottom:4px;">현대그린푸드 재경팀</div>
                <div style="font-size:21px;font-weight:600;color:#ffffff;letter-spacing:0.231px;">마감 일정 알림</div>
            </div>
            <div style="margin-left:auto;">
                <span style="background:{urgency_color};color:#ffffff;font-size:12px;font-weight:600;padding:5px 14px;border-radius:9999px;letter-spacing:-0.12px;">
                    {urgency_text} 마감
                </span>
            </div>
        </div>
        <div style="padding:40px 40px 32px;">
            <p style="font-size:17px;color:#1d1d1f;margin:0 0 8px;font-weight:400;line-height:1.47;letter-spacing:-0.374px;">안녕하세요.</p>
            <p style="font-size:17px;color:#1d1d1f;margin:0 0 32px;line-height:1.47;letter-spacing:-0.374px;">
                {"오늘" if days == 0 else "내일" if days == 1 else f"{days}일 후"} 마감되는 담당 일정이 있습니다.
            </p>
            <table style="width:100%;border-collapse:collapse;">
                <thead>
                    <tr style="border-bottom:1px solid #1d1d1f;">
                        <th style="padding:0 0 10px;text-align:left;font-size:12px;color:#86868b;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;">제목</th>
                        <th style="padding:0 16px 10px;text-align:left;font-size:12px;color:#86868b;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;">카테고리</th>
                        <th style="padding:0 16px 10px;text-align:left;font-size:12px;color:#86868b;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;">법인/지사</th>
                        <th style="padding:0 0 10px;text-align:right;font-size:12px;color:#86868b;font-weight:600;letter-spacing:0.05em;text-transform:uppercase;">마감일</th>
                    </tr>
                </thead>
                <tbody>
                    {rows}
                </tbody>
            </table>
            <div style="margin-top:40px;text-align:center;">
                <a href="https://hyundaigreenfood.framer.website/달력"
                   style="display:inline-block;background:#0066cc;color:#ffffff;text-decoration:none;font-size:17px;font-weight:400;padding:11px 28px;border-radius:9999px;letter-spacing:-0.374px;">
                    캘린더에서 확인하기
                </a>
            </div>
        </div>
        <div style="height:1px;background:#f0f0f0;margin:0 40px;"></div>
        <div style="padding:20px 40px 32px;">
            <p style="font-size:12px;color:#86868b;margin:0;line-height:1.5;letter-spacing:-0.12px;">
                이 메일은 현대그린푸드 재경팀 대시보드에서 자동 발송됩니다.<br>
                문의사항은 재경팀으로 연락해주세요.
            </p>
        </div>
    </div>
</body>
</html>"""


# ── API 엔드포인트 ─────────────────────────────────────────────────────────────

@router.post("/notify/dday")
async def notify_dday(secret: str = ""):
    """
    D-15, D-7, D-1, D-0 마감 일정 알림 발송 (이메일 및 팀즈)
    Railway Cron: POST /notify/dday?secret=YOUR_SECRET
    """
    if CRON_SECRET and secret != CRON_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")

    results = {"email_sent": [], "teams_sent": [], "skipped": [], "errors": []}
    manager_emails = await get_manager_emails()

    for days in [15, 7, 1, 0]:
        schedules = await get_schedules_by_dday(days)
        if not schedules:
            continue

        # created_by(담당자 이름) 기준으로 그룹핑
        by_user: dict[str, list] = {}
        no_owner = []
        
        for s in schedules:
            owner = s.get("created_by", "").strip()
            if owner:
                by_user.setdefault(owner, []).append(s)
            else:
                no_owner.append(s)

        # 담당자별 발송 진행
        for user_name, user_schedules in by_user.items():
            user_info = await get_user_info(user_name)
            if not user_info:
                results["skipped"].append(f"{user_name} (Supabase 유저 정보 없음)")
                continue

            email = user_info.get("email")
            emp_id = user_info.get("employee_id") # 주소록 조회의 치트키 '사번'

            # 1. 메시지 텍스트 조립
            day_text = "오늘" if days == 0 else "내일" if days == 1 else f"{days}일 후"
            titles_text = ", ".join([f"'{s.get('title')}'" for s in user_schedules])
            teams_msg = f"'{user_name}' 선임님, 담당하신 {titles_text} 업무 마감일이 {day_text}입니다. 마감 기한을 준수해 주세요!"

            # 2. 팀즈 발송 (사번이 있을 때만 실행)
            if emp_id:
                teams_ok = await send_teams_notification(emp_id, teams_msg)
                if teams_ok:
                    results["teams_sent"].append(f"{user_name}(사번:{emp_id}) 팀즈 DM 전송 완료")
                else:
                    results["errors"].append(f"{user_name} 팀즈 발송 실패")
            else:
                results["skipped"].append(f"{user_name} (사번 데이터 누락으로 팀즈 스킵)")

            # 3. 기존 이메일 발송 로직 유지
            if email and RESEND_API_KEY:
                if days == 0:
                    subject = f"[재경팀] ⚠ 오늘 마감 일정 알림 — {len(user_schedules)}건"
                elif days == 1:
                    subject = f"[재경팀] ⚠ 내일 마감 일정 알림 — {len(user_schedules)}건"
                else:
                    subject = f"[재경팀] D-{days} 마감 일정 알림 — {len(user_schedules)}건"
                
                html = build_email_html(user_schedules, days)
                email_ok = await send_email(email, subject, html, cc=manager_emails)

                if email_ok:
                    results["email_sent"].append(f"{user_name} ({email}) 이메일 완료")
                else:
                    results["errors"].append(f"{user_name} ({email}) 이메일 실패")

        if no_owner:
            results["skipped"].append(f"created_by 없음 — {len(no_owner)}건 미발송")

    return {
        "date": date.today().isoformat(),
        "result": results,
    }
