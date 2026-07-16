from django.core.management import call_command
from django.test import TestCase
from io import StringIO
from api.models import ApiUser, CarMake, CarModel, UserCarModel


class AutofillSupplierCarModelsTestCase(TestCase):
    def setUp(self):
        # Clear database to prevent conflict with seeded migrations data
        ApiUser.objects.all().delete()
        CarMake.objects.all().delete()

        # Create Car Makes
        self.toyota = CarMake.objects.create(name="Toyota", slug="toyota")
        self.bmw = CarMake.objects.create(name="BMW", slug="bmw")
        self.audi = CarMake.objects.create(name="Audi", slug="audi")

        # Create Toyota models (all active)
        self.corolla = CarModel.objects.create(make=self.toyota, name="Corolla", slug="corolla", is_active=True)
        self.camry = CarModel.objects.create(make=self.toyota, name="Camry", slug="camry", is_active=True)
        self.hilux = CarModel.objects.create(make=self.toyota, name="Hilux", slug="hilux", is_active=True)

        # Create BMW models (one active, one inactive)
        self.m3 = CarModel.objects.create(make=self.bmw, name="M3", slug="m3", is_active=True)
        self.x5 = CarModel.objects.create(make=self.bmw, name="X5", slug="x5", is_active=True)
        self.i3_inactive = CarModel.objects.create(make=self.bmw, name="I3 Inactive", slug="i3-inactive", is_active=False)

        # Create Audi models (active)
        self.a4 = CarModel.objects.create(make=self.audi, name="A4", slug="a4", is_active=True)

        # Create Suppliers
        self.supplier1 = ApiUser.objects.create_user(
            phone="+966555000001",
            name="Supplier One",
            role=ApiUser.ROLE_SUPPLIER,
        )
        self.supplier2 = ApiUser.objects.create_user(
            phone="+966555000002",
            name="Supplier Two",
            role=ApiUser.ROLE_SUPPLIER,
        )

        # Create normal user
        self.normal_user = ApiUser.objects.create_user(
            phone="+966555000003",
            name="Regular User",
            role=ApiUser.ROLE_USER,
        )

        # Associate supplier1 with Toyota Corolla and BMW M3
        UserCarModel.objects.create(user=self.supplier1, car_model=self.corolla)
        UserCarModel.objects.create(user=self.supplier1, car_model=self.m3)

        # Associate supplier2 with nothing initially
        # Associate normal_user with Audi A4 (should not be affected as they are not a supplier)
        UserCarModel.objects.create(user=self.normal_user, car_model=self.a4)

    def test_dry_run_does_not_modify_database(self):
        initial_count = UserCarModel.objects.count()
        
        out = StringIO()
        call_command("autofill_supplier_car_models", "--dry-run", stdout=out)
        
        # Count should remain the same
        self.assertEqual(UserCarModel.objects.count(), initial_count)
        
        output = out.getvalue()
        self.assertIn("Supplier One", output)
        self.assertIn("Would add 2 models", output)  # Camry and Hilux for Toyota
        self.assertIn("Would add 1 models", output)  # X5 for BMW (not i3_inactive)
        self.assertIn("Dry run complete", output)

    def test_autofill_supplier_car_models_adds_missing_active_models(self):
        out = StringIO()
        call_command("autofill_supplier_car_models", stdout=out)
        
        output = out.getvalue()
        self.assertIn("Added 2 models", output)
        self.assertIn("Added 1 models", output)
        self.assertIn("Finished", output)

        # Verify supplier1 now supports Corolla, Camry, Hilux, M3, X5
        supported_model_ids = set(
            UserCarModel.objects.filter(user=self.supplier1).values_list("car_model_id", flat=True)
        )
        expected_model_ids = {
            self.corolla.id,
            self.camry.id,
            self.hilux.id,
            self.m3.id,
            self.x5.id,
        }
        self.assertEqual(supported_model_ids, expected_model_ids)

        # Verify supplier2 still has no models (didn't have any models initially, so make was not chosen)
        self.assertFalse(UserCarModel.objects.filter(user=self.supplier2).exists())

        # Verify normal_user still has only A4
        normal_user_models = set(
            UserCarModel.objects.filter(user=self.normal_user).values_list("car_model_id", flat=True)
        )
        self.assertEqual(normal_user_models, {self.a4.id})

    def test_autofill_filtered_by_user_id(self):
        # Create third supplier who supports Corolla
        supplier3 = ApiUser.objects.create_user(
            phone="+966555000004",
            name="Supplier Three",
            role=ApiUser.ROLE_SUPPLIER,
        )
        UserCarModel.objects.create(user=supplier3, car_model=self.corolla)

        out = StringIO()
        call_command("autofill_supplier_car_models", f"--user-id={self.supplier1.id}", stdout=out)

        output = out.getvalue()
        self.assertIn("Processing 1 suppliers", output)
        self.assertIn("Supplier One", output)
        self.assertNotIn("Supplier Three", output)

        # Verify supplier1 got updated
        supplier1_model_ids = set(
            UserCarModel.objects.filter(user=self.supplier1).values_list("car_model_id", flat=True)
        )
        self.assertIn(self.camry.id, supplier1_model_ids)

        # Verify supplier3 did not get updated
        supplier3_model_ids = set(
            UserCarModel.objects.filter(user=supplier3).values_list("car_model_id", flat=True)
        )
        self.assertEqual(supplier3_model_ids, {self.corolla.id})

    def test_autofill_filtered_by_make_slug(self):
        out = StringIO()
        call_command("autofill_supplier_car_models", "--make-slug=toyota", stdout=out)

        output = out.getvalue()
        # Toyota should be processed (adds Camry, Hilux)
        self.assertIn("active models for make 'Toyota'", output)
        # BMW should not be processed (would have added X5)
        self.assertNotIn("active models for make 'BMW'", output)

        # Verify supplier1 supported models
        supported_model_ids = set(
            UserCarModel.objects.filter(user=self.supplier1).values_list("car_model_id", flat=True)
        )
        # Should have Corolla, Camry, Hilux (Toyota) + M3 (BMW, unchanged)
        # But not X5 (BMW, which was excluded by make filter)
        self.assertIn(self.camry.id, supported_model_ids)
        self.assertIn(self.hilux.id, supported_model_ids)
        self.assertNotIn(self.x5.id, supported_model_ids)
