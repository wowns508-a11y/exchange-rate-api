"""
재경팀 마감 일정 이메일 알림
Railway FastAPI에 추가할 라우터
- 매일 오전 9시 Railway Cron으로 호출
- D-7, D-15 일정 조회 → 담당자 이메일 발송 (Resend)
"""

from fastapi import APIRouter, HTTPException
from datetime import date, timedelta
import httpx
import os

router = APIRouter()

# ── 환경변수 (Railway Variables에 등록) ─────────────────────────────────────
SUPABASE_URL    = os.getenv("SUPABASE_URL", "https://aaexsceexmzdufwyxjvu.supabase.co")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY", "")   # service_role key
RESEND_API_KEY  = os.getenv("RESEND_API_KEY", "")         # Resend API key
FROM_EMAIL      = os.getenv("FROM_EMAIL", "재경팀 <noreply@yourdomain.com>")
CRON_SECRET     = os.getenv("CRON_SECRET", "")            # 무단 호출 방지용 시크릿


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
        f"{SUPABASE_URL}/rest/v1/schedules"
        f"?due_date=eq.{target_date}"
        f"&is_done=eq.false"
        f"&category=neq.공휴일"
        f"&select=*"
    )
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=sb_headers())
        r.raise_for_status()
        return r.json()


async def get_user_email(user_name: str) -> str | None:
    """
    public.users 테이블에서 name으로 email 조회
    스키마: id, email, name, employee_id, approved, created_at, password
    """
    url = (
        f"{SUPABASE_URL}/rest/v1/users"
        f"?name=eq.{user_name}"
        f"&approved=eq.true"
        f"&select=email"
        f"&limit=1"
    )
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=sb_headers())
        if r.status_code == 200 and r.json():
            return r.json()[0].get("email")
    return None


async def get_all_staff_emails() -> list[dict]:
    """
    전체 직원 이메일 목록 조회 (created_by 매핑 안 될 때 fallback)
    users 테이블: id, name, email
    """
    url = f"{SUPABASE_URL}/rest/v1/users?select=name,email&approved=eq.true"
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=sb_headers())
        if r.status_code == 200:
            return r.json()
    return []


# ── 이메일 발송 (Resend) ───────────────────────────────────────────────────────

async def send_email(to: str, subject: str, html: str) -> bool:
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": FROM_EMAIL,
                "to": [to],
                "subject": subject,
                "html": html,
            },
        )
        return r.status_code == 200


def build_email_html(schedules: list[dict], days: int) -> str:
    """이메일 HTML 생성"""
    rows = ""
    for s in schedules:
        due = str(s.get("due_date", ""))[:10]
        rows += f"""
        <tr>
            <td style="padding:10px 14px;border-bottom:1px solid #f1f5f9;font-size:14px;color:#1e293b;font-weight:600;">
                {s.get("title", "")}
            </td>
            <td style="padding:10px 14px;border-bottom:1px solid #f1f5f9;font-size:13px;color:#64748b;">{s.get("category", "")}</td>
            <td style="padding:10px 14px;border-bottom:1px solid #f1f5f9;font-size:13px;color:#64748b;">{s.get("target_entity", "")}</td>
            <td style="padding:10px 14px;border-bottom:1px solid #f1f5f9;font-size:13px;color:#ef4444;font-weight:700;">{due}</td>
        </tr>
        """

    urgency_color = "#ef4444" if days <= 7 else "#3b82f6"
    urgency_text  = f"D-{days}"

    return f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head><meta charset="UTF-8"></head>
    <body style="margin:0;padding:0;background:#f8fafc;font-family:'Pretendard',system-ui,sans-serif;">
        <div style="max-width:560px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08);">
            
            <!-- 헤더 -->
            <div style="background:#1e293b;padding:28px 32px;">
                <div style="font-size:12px;color:#94a3b8;letter-spacing:0.05em;margin-bottom:6px;">현대그린푸드 재경팀</div>
                <div style="font-size:22px;font-weight:800;color:#fff;">📋 마감 일정 알림</div>
                <div style="margin-top:10px;">
                    <span style="background:{urgency_color};color:#fff;font-size:13px;font-weight:700;padding:4px 12px;border-radius:999px;">
                        {urgency_text} 마감 임박
                    </span>
                </div>
            </div>

            <!-- 본문 -->
            <div style="padding:28px 32px;">
                <p style="font-size:15px;color:#334155;margin:0 0 20px;">
                    안녕하세요.<br>
                    담당하신 일정 중 <strong style="color:{urgency_color};">{days}일 후</strong> 마감되는 항목이 있습니다.
                </p>

                <!-- 일정 테이블 -->
                <table style="width:100%;border-collapse:collapse;border:1px solid #e2e8f0;border-radius:10px;overflow:hidden;">
                    <thead>
                        <tr style="background:#f8fafc;">
                            <th style="padding:10px 14px;text-align:left;font-size:12px;color:#94a3b8;font-weight:600;border-bottom:1px solid #e2e8f0;">제목</th>
                            <th style="padding:10px 14px;text-align:left;font-size:12px;color:#94a3b8;font-weight:600;border-bottom:1px solid #e2e8f0;">카테고리</th>
                            <th style="padding:10px 14px;text-align:left;font-size:12px;color:#94a3b8;font-weight:600;border-bottom:1px solid #e2e8f0;">법인/지사</th>
                            <th style="padding:10px 14px;text-align:left;font-size:12px;color:#94a3b8;font-weight:600;border-bottom:1px solid #e2e8f0;">마감일</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>

                <!-- CTA -->
                <div style="margin-top:24px;text-align:center;">
                    <a href="https://hyundaigreenfood.framer.website/달력"
                       style="display:inline-block;background:#1e293b;color:#fff;text-decoration:none;font-size:14px;font-weight:700;padding:12px 28px;border-radius:10px;">
                        캘린더에서 확인하기 →
                    </a>
                </div>
            </div>

            <!-- 푸터 -->
            <div style="padding:16px 32px;background:#f8fafc;border-top:1px solid #f1f5f9;">
                <p style="font-size:11px;color:#94a3b8;margin:0;text-align:center;">
                    이 메일은 현대그린푸드 재경팀 대시보드에서 자동 발송됩니다.
                </p>
            </div>
        </div>
    </body>
    </html>
    """


# ── API 엔드포인트 ─────────────────────────────────────────────────────────────

@router.post("/notify/dday")
async def notify_dday(secret: str = ""):
    """
    D-7, D-15 마감 일정 이메일 알림 발송
    Railway Cron: POST /notify/dday?secret=YOUR_SECRET
    """
    # 시크릿 검증 (무단 호출 방지)
    if CRON_SECRET and secret != CRON_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")

    if not RESEND_API_KEY:
        raise HTTPException(status_code=500, detail="RESEND_API_KEY not set")

    results = {"sent": [], "skipped": [], "errors": []}

    for days in [7, 15]:
        schedules = await get_schedules_by_dday(days)
        if not schedules:
            continue

        # created_by 기준으로 그룹핑
        by_user: dict[str, list] = {}
        no_owner = []
        for s in schedules:
            owner = s.get("created_by", "").strip()
            if owner:
                by_user.setdefault(owner, []).append(s)
            else:
                no_owner.append(s)

        # 담당자별 이메일 발송
        for user_name, user_schedules in by_user.items():
            email = await get_user_email(user_name)
            if not email:
                results["skipped"].append(f"{user_name} (이메일 없음)")
                continue

            subject = f"[재경팀] D-{days} 마감 일정 알림 — {len(user_schedules)}건"
            html    = build_email_html(user_schedules, days)
            ok      = await send_email(email, subject, html)

            if ok:
                results["sent"].append(f"{user_name} ({email}) — {len(user_schedules)}건")
            else:
                results["errors"].append(f"{user_name} ({email}) 발송 실패")

        # created_by 없는 일정은 전체 공지 (선택)
        if no_owner:
            results["skipped"].append(f"created_by 없음 — {len(no_owner)}건 미발송")

    return {
        "date": date.today().isoformat(),
        "result": results,
    }


@router.get("/notify/preview")
async def preview_email(days: int = 7):
    """이메일 미리보기 (개발용)"""
    schedules = await get_schedules_by_dday(days)
    html = build_email_html(schedules, days)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)
