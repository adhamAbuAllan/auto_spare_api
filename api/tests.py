from rest_framework.test import APITestCase
from .models import ApiUser, SparePart


class UsersApiTests(APITestCase):
    def test_create_user(self):
        response = self.client.post(
            "/api/users/",
            data={
                "name": "Alice",
                "phone": "+966555000111",
                "city": "Riyadh",
                "role": "user",
                "rating": "4.50",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ApiUser.objects.count(), 1)
        self.assertEqual(ApiUser.objects.first().name, "Alice")
        self.assertEqual(ApiUser.objects.first().role, "user")

    def test_list_users(self):
        ApiUser.objects.create(
            name="Alice",
            phone="+966555000111",
            city="Riyadh",
            role="user",
            rating="4.50",
        )
        ApiUser.objects.create(
            name="Bob",
            phone="+966555000222",
            city="Jeddah",
            role="supplier",
            rating="4.75",
        )

        response = self.client.get("/api/users/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["role"], "user")
        self.assertEqual(payload[1]["role"], "supplier")


class SparePartApiTests(APITestCase):
    def test_create_spare_part(self):
        response = self.client.post(
            "/api/spare-parts/",
            data={
                "name": "Brake Pad",
                "description": "Front wheel brake pad",
                "price": "149.99",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SparePart.objects.count(), 1)
        self.assertEqual(SparePart.objects.first().name, "Brake Pad")

    def test_list_spare_parts(self):
        SparePart.objects.create(name="Oil Filter", description="", price="45.00")
        SparePart.objects.create(name="Air Filter", description="", price="65.50")

        response = self.client.get("/api/spare-parts/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["name"], "Oil Filter")
        self.assertEqual(payload[1]["name"], "Air Filter")
