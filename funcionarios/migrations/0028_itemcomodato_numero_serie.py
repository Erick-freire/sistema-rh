from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('funcionarios', '0027_catalogo_comodato'),
    ]

    operations = [
        migrations.RenameField(
            model_name='itemcomodato',
            old_name='codigo',
            new_name='numero_serie',
        ),
        migrations.AlterField(
            model_name='itemcomodato',
            name='numero_serie',
            field=models.CharField(blank=True, max_length=50),
        ),
    ]
