"""Real HA form serialization and source validation for split prices."""
import json
from types import SimpleNamespace
import pytest
pytest.importorskip('homeassistant')
from homeassistant.helpers.data_entry_flow import FlowManagerIndexView
from homeassistant.data_entry_flow import FlowResultType
from custom_components.battery_optimizer import config_flow as flow, const as c
from custom_components.battery_optimizer.money import locale_money
MONEY=locale_money(override='',ha_currency='EUR',time_zone='Europe/Helsinki',country='FI')


@pytest.mark.parametrize('source',['import','sensor','emaldo'])
def test_export_selector_roundtrip(source):
    defaults={c.CONF_PRICE_SOURCE:'sensor',c.CONF_SPOT_SENSOR:'input_number.contract',c.CONF_SELL_PRICE_SOURCE:source,c.CONF_SELL_PRICE_SENSOR:'sensor.market'}
    schema=flow._build_schema(defaults,emaldo_options={'battery':'Battery'},money=MONEY)
    result=FlowManagerIndexView(None)._prepare_result_json({'type':FlowResultType.FORM,'data_schema':schema})
    fields={f['name']:f for f in json.loads(json.dumps(result))['data_schema']}
    assert fields[c.CONF_SELL_PRICE_SOURCE]['default']==source
    assert fields[c.CONF_SELL_PRICE_SOURCE]['options']==[['import','same as input source'],['emaldo','emaldo'],['sensor','sensor']]
    assert schema(defaults)[c.CONF_SPOT_SENSOR]=='input_number.contract'
    assert 'static_price' not in fields


def test_default_and_active_sensor_validation():
    schema=flow._build_schema(None,emaldo_options={'battery':'Battery'},money=MONEY)
    assert schema({})[c.CONF_SELL_PRICE_SOURCE]=='import'
    hass=SimpleNamespace(states=SimpleNamespace(get=lambda entity: entity=='input_number.contract'))
    values={c.CONF_PRICE_SOURCE:'sensor',c.CONF_SPOT_SENSOR:'input_number.contract',c.CONF_SELL_PRICE_SOURCE:'emaldo',c.CONF_SELL_PRICE_SENSOR:'sensor.missing'}
    assert flow._price_errors(hass,values)=={}
    values[c.CONF_SELL_PRICE_SOURCE]='sensor'
    assert flow._price_errors(hass,values)=={c.CONF_SELL_PRICE_SENSOR:'sensor_not_found'}
    values[c.CONF_SPOT_SENSOR]=''
    assert c.CONF_SPOT_SENSOR in flow._price_errors(hass,values)
