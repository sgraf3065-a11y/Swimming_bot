"""
Запустить ОДИН РАЗ перед первым стартом бота.
Аутентифицирует Google Calendar и показывает доступные календари.
"""
from calendar_service import get_service

print("Аутентификация Google Calendar...")
service = get_service()
print("✅ Успешно!")

calendars = service.calendarList().list().execute()
print("\nДоступные календари:")
for cal in calendars.get("items", []):
    print(f"  {cal['summary']:40s}  id: {cal['id']}")

print("\nСкопируйте нужный id в .env → GOOGLE_CALENDAR_ID")
