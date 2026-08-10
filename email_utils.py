# email_utils.py
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

# Default configuration (can be overridden via environment variables)
DEFAULT_RECIPIENT = os.environ.get('DEFAULT_ALERT_RECIPIENT', 'pereira.taylor@cleanharbors.com')
SMTP_SERVER = os.environ.get('SMTP_SERVER', 'localhost')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '25'))
SMTP_SENDER = os.environ.get('SMTP_SENDER', 'trucklog-alerts@cleanharbors.com')

LOG_FILE = os.path.join(os.path.dirname(__file__), 'email_alerts.log')

def log_alert_fallback(subject, body, recipients):
    """Fallback logger when SMTP server is unreachable or offline."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log_entry = f"[{timestamp}] RECIPIENTS: {recipients}\nSUBJECT: {subject}\nBODY:\n{body}\n{'-'*60}\n"
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_entry)
    except Exception as e:
        print(f"Failed to write to email_alerts.log: {e}")

def send_via_outlook_app(subject, body, recipients, is_html=False):
    """Sends email natively using local Windows Outlook COM object with UTF-8 base64 encoding."""
    import subprocess
    import tempfile
    import base64
    
    recipient_str = "; ".join(recipients)
    body_prop = "HTMLBody" if is_html else "Body"
    
    subj_b64 = base64.b64encode(subject.encode('utf-8')).decode('ascii')
    body_b64 = base64.b64encode(body.encode('utf-8')).decode('ascii')
    
    ps_code = f"""
$subjBytes = [System.Convert]::FromBase64String("{subj_b64}")
$subj = [System.Text.Encoding]::UTF8.GetString($subjBytes)

$bodyBytes = [System.Convert]::FromBase64String("{body_b64}")
$body = [System.Text.Encoding]::UTF8.GetString($bodyBytes)

$Outlook = New-Object -ComObject Outlook.Application
$Mail = $Outlook.CreateItem(0)
$Mail.To = "{recipient_str}"
$Mail.Subject = $subj
$Mail.{body_prop} = $body
$Mail.Send()
Write-Output "SUCCESS_OUTLOOK_SENT"
"""
    try:
        with tempfile.NamedTemporaryFile(suffix='.ps1', mode='w', encoding='utf-8', delete=False) as tf:
            tf.write(ps_code)
            temp_ps1 = tf.name
            
        res = subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-File', temp_ps1],
            capture_output=True, text=True, timeout=15
        )
        if os.path.exists(temp_ps1):
            try:
                os.remove(temp_ps1)
            except Exception:
                pass
        if "SUCCESS_OUTLOOK_SENT" in res.stdout:
            print(f"Email sent successfully via Outlook app: '{subject}' to {recipients}")
            return True
        else:
            print(f"Outlook app script returned: {res.stdout} {res.stderr}")
    except Exception as err:
        print(f"Outlook COM dispatch warning: {err}")
    return False

def send_email_alert(subject, body, recipients=None, is_html=False):
    """
    Sends an email alert using local Windows Outlook App (primary) or SMTP. 
    If both fail or are unconfigured, logs the alert cleanly to email_alerts.log.
    """
    if recipients is None:
        recipients = [DEFAULT_RECIPIENT]
    elif isinstance(recipients, str):
        recipients = [recipients]

    # 1. Try sending via local Windows Outlook App (no SMTP server configuration required!)
    if send_via_outlook_app(subject, body, recipients, is_html=is_html):
        return True

    # 2. Fallback to standard SMTP server
    try:
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From'] = SMTP_SENDER
        msg['To'] = ", ".join(recipients)

        mime_type = 'html' if is_html else 'plain'
        msg.attach(MIMEText(body, mime_type, 'utf-8'))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=5)
        server.sendmail(SMTP_SENDER, recipients, msg.as_string())
        server.quit()
        print(f"Email sent successfully via SMTP: {subject} to {recipients}")
        return True
    except Exception as e:
        print(f"SMTP notification warning ({e}). Logging alert to fallback log.")
        log_alert_fallback(subject, body, recipients)
        return False

def generate_and_send_las_digest(target_date=None, recipient=None):
    """
    Generates and sends a daily LAS summary digest covering:
    1. Received LAS loads today missing lab test/approval updates.
    2. Scheduled LAS loads today that were NOT received at scale.
    """
    from contextlib import closing
    from database import get_db_connection
    from schedule_utils import calculate_las_tags

    if not target_date:
        target_date = datetime.now().strftime('%Y-%m-%d')
    if not recipient:
        recipient = DEFAULT_RECIPIENT

    received_untested = []
    scheduled_unreceived = []

    with closing(get_db_connection()) as conn:
        # 1. Query Received Trucks Today
        received_raw = conn.execute('''
            SELECT * FROM truck_logs 
            WHERE date_received = ? AND UPPER(COALESCE(test_status, '')) NOT IN ('VOID', 'VOIDED')
            ORDER BY id DESC
        ''', (target_date,)).fetchall()
        received_logs = [dict(r) for r in received_raw]

        prof_nums = list({str(l.get('profile_number') or '').strip().upper() for l in received_logs if l.get('profile_number')})
        
        # Also include schedule profiles for today
        schedule_raw = conn.execute('''
            SELECT ds.*, 
                   COALESCE(NULLIF(TRIM(w.expiration_date), ''), p.expiration_date) AS expiration_date, 
                   CASE 
                       WHEN w.status IN ('Approved', 'Recertified', 'Complete', 'Released', 'ACTIVE') THEN 'ACTIVE'
                       WHEN w.status IN ('Expired', 'Needs Review', 'Rejected', 'INACTIVE') THEN 'INACTIVE'
                       ELSE p.status 
                   END AS profile_status, 
                   p.special_handling,
                   p.voc_percentage AS profile_voc_percentage
            FROM daily_schedule ds
            LEFT JOIN profiles p ON TRIM(UPPER(ds.profile_number)) = TRIM(UPPER(p.profile_number))
            LEFT JOIN waste_acceptance_log w ON TRIM(UPPER(ds.profile_number)) = TRIM(UPPER(w.profile_number)) AND COALESCE(w.is_archived, 0) = 0
            WHERE ds.schedule_date = ?
        ''', (target_date,)).fetchall()
        schedule_loads = [dict(s) for s in schedule_raw]

        for s in schedule_loads:
            pn = str(s.get('profile_number') or '').strip().upper()
            if pn and pn not in prof_nums:
                prof_nums.append(pn)

        profile_map = {}
        wa_map = {}
        if prof_nums:
            placeholders = ','.join(['?'] * len(prof_nums))
            p_rows = conn.execute(f'''
                SELECT profile_number, generator, win_code, status, expiration_date, special_handling, voc_percentage
                FROM profiles
                WHERE TRIM(UPPER(profile_number)) IN ({placeholders})
            ''', prof_nums).fetchall()
            for pr in p_rows:
                key = str(pr['profile_number']).strip().upper()
                profile_map[key] = dict(pr)

            wa_rows = conn.execute(f'''
                SELECT profile_number, status 
                FROM waste_acceptance_log
                WHERE TRIM(UPPER(profile_number)) IN ({placeholders}) AND COALESCE(is_archived, 0) = 0
            ''', prof_nums).fetchall()
            for wa in wa_rows:
                key = str(wa['profile_number']).strip().upper()
                wa_map[key] = wa['status']

        # Evaluate Received Untested LAS Loads
        received_profiles_set = set()
        for log in received_logs:
            p_key = str(log.get('profile_number') or '').strip().upper()
            received_profiles_set.add(p_key)
            p_info = profile_map.get(p_key, {})
            eval_load = {
                'profile_number': p_key,
                'routing_code': p_info.get('win_code', log.get('win_code', '')),
                'special_notes': log.get('notes', ''),
                'special_handling': p_info.get('special_handling', ''),
                'profile_status': p_info.get('status', ''),
                'expiration_date': p_info.get('expiration_date', ''),
                'voc_level': str(log.get('voc_percentage', '')),
                'profile_voc_percentage': p_info.get('voc_percentage')
            }
            tags = calculate_las_tags(eval_load)
            is_approved = (p_info.get('status') == 'ACTIVE') or (wa_map.get(p_key) in ['Approved', 'Recertified', 'Complete', 'Released', 'ACTIVE'])
            if len(tags) > 0 and not is_approved:
                log['las_tags'] = tags
                log['generator'] = p_info.get('generator', log.get('generator', ''))
                received_untested.append(log)

        # Evaluate Scheduled LAS Loads Not Received
        for s in schedule_loads:
            p_key = str(s.get('profile_number') or '').strip().upper()
            tags = calculate_las_tags(s)
            if len(tags) > 0 and p_key not in received_profiles_set:
                s['las_tags'] = tags
                scheduled_unreceived.append(s)

    # Format HTML Body
    html_lines = [
        f"<h2 style='color: #333; font-family: Arial, sans-serif;'>[LAS DIGEST] Daily LAS Summary Digest - {target_date}</h2>",
        f"<p style='font-family: Arial, sans-serif;'><strong>Report Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>",
        "<hr style='border: 1px solid #ccc;'>"
    ]

    html_lines.append(f"<h3 style='color: #d9534f; font-family: Arial, sans-serif;'>[ATTENTION] 1. Received LAS Loads Missing Lab/Profile Updates ({len(received_untested)})</h3>")
    if received_untested:
        html_lines.append("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse: collapse; font-family: Arial, sans-serif;'>")
        html_lines.append("<tr style='background-color: #d9534f; color: white;'><th>Ticket #</th><th>Manifest #</th><th>Profile #</th><th>Generator</th><th>LAS Tags</th></tr>")
        for r in received_untested:
            html_lines.append(f"<tr><td>{r.get('truck_id') or r.get('load_number') or '---'}</td><td>{r.get('manifest_number') or '---'}</td><td><strong>{r.get('profile_number')}</strong></td><td>{r.get('generator') or '---'}</td><td><span style='color: #d9534f; font-weight: bold;'>{', '.join(r.get('las_tags', []))}</span></td></tr>")
        html_lines.append("</table>")
    else:
        html_lines.append("<p style='color: #28a745; font-weight: bold; font-family: Arial, sans-serif;'>[OK] All received LAS trucks today have updated lab/approval status!</p>")

    html_lines.append(f"<h3 style='margin-top: 25px; color: #0275d8; font-family: Arial, sans-serif;'>[SCHEDULED] 2. Scheduled LAS Loads NOT Received ({len(scheduled_unreceived)})</h3>")
    if scheduled_unreceived:
        html_lines.append("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse: collapse; font-family: Arial, sans-serif;'>")
        html_lines.append("<tr style='background-color: #0275d8; color: white;'><th>Profile #</th><th>Generator</th><th>WIN</th><th>Loads Scheduled</th><th>LAS Tags</th></tr>")
        for s in scheduled_unreceived:
            html_lines.append(f"<tr><td><strong>{s.get('profile_number')}</strong></td><td>{s.get('generator') or '---'}</td><td>{s.get('routing_code') or '---'}</td><td align='center'>{s.get('load_count', 1)}</td><td><span style='color: #d9534f; font-weight: bold;'>{', '.join(s.get('las_tags', []))}</span></td></tr>")
        html_lines.append("</table>")
    else:
        html_lines.append("<p style='color: #28a745; font-weight: bold; font-family: Arial, sans-serif;'>[OK] All scheduled LAS loads for today have been received!</p>")

    html_body = "".join(html_lines)
    subj = f"[LAS DIGEST] Daily LAS Summary Digest - {target_date} ({len(received_untested)} Untested Received | {len(scheduled_unreceived)} Unreceived Scheduled)"

    return send_email_alert(subj, html_body, recipients=[recipient], is_html=True)
