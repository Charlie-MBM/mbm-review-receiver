def send_review_sms(name, phone_number):
    """
    Send review SMS to patient.
    Updated body to fix iOS contact extraction issue:
    - Leading with 'Mt. Baker Medical' makes iOS extract the practice name as contact
    - Including 'James was wondering' preserves personal tone
    """
    body = f"Hi {name}, this is Mt. Baker Medical. Thanks for being a patient. James was wondering if you'd mind sharing your experience?..."
    return send_sms(phone_number, body)