from django.db import migrations


# Quem tinha papel 'rh' até aqui é, na prática, o time de DP. O RH de fato
# (que vê também os módulos exclusivos) é marcado depois, pelo admin, com
# papel 'rh' ou com acesso_rh.
def rh_vira_dp(apps, schema_editor):
    Usuario = apps.get_model('funcionarios', 'Usuario')
    Usuario.objects.filter(papel='rh').update(papel='dp')


def dp_volta_rh(apps, schema_editor):
    Usuario = apps.get_model('funcionarios', 'Usuario')
    Usuario.objects.filter(papel='dp').update(papel='rh')


class Migration(migrations.Migration):

    dependencies = [
        ('funcionarios', '0031_papeis_dp_seguranca_e_epi'),
    ]

    operations = [
        migrations.RunPython(rh_vira_dp, dp_volta_rh),
    ]
