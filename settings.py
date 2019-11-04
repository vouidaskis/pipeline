import os

arches_models = {
	'Destruction': '39ca28c0-643c-4b66-abb4-74b901a7d8fc',
	'Event': 'a93a8a1b-383e-41d2-ac8b-2f18a85b3759',
	'Group': '00000000-0000-0000-0000-000000000000',
	'Journal': 'f6c52114-aca7-4e2b-a51a-5d61e4c7181c',
	'Place': '846cdf96-f0f5-4310-8415-018452032175',
	'Procurement': '08ad7e74-da48-4720-9e3f-ad5577d0d57c',
	'Series': 'd537c62e-720a-467f-ac88-9f0b53b14dd8',
	"Acquisition": "b5fdce59-2e41-11e9-b1c2-a4d18cec433a",
	"Activity": "24c45975-3955-11e9-80f0-a4d18cec433a",
	"HumanMadeObject": "2486c17d-2e42-11e9-bd33-a4d18cec433a",
	"LinguisticObject": "41a41e47-2e42-11e9-b5ee-a4d18cec433a",
	"Organization": "edbee5e8-2e41-11e9-bc39-a4d18cec433a",
	"Person": "0b47366e-2e42-11e9-9018-a4d18cec433a",
	"Phase": "17871ac7-2e42-11e9-87b2-a4d18cec433a",
	"Set": "76510a15-d517-4ef6-9b57-2b87d4540250",
	"VisualItem": "504dcf0a-2e42-11e9-b4e2-a4d18cec433a"
}

arches_endpoint = os.environ.get('GETTY_PIPELINE_ARCHES_ENDPOINT', 'http://localhost:8001/resources/')
arches_endpoint_username = os.environ.get('GETTY_PIPELINE_ARCHES_USERNAME', 'admin')
arches_endpoint_password = os.environ.get('GETTY_PIPELINE_ARCHES_PASSWORD', 'admin')
arches_auth_endpoint = os.environ.get('GETTY_PIPELINE_ARCHES_AUTH_ENDPOINT', 'http://localhost:8001/o/token/')
arches_client_id = os.environ.get('GETTY_PIPELINE_ARCHES_CLIENT_ID', 'OaGs0HfnBNd2VpI4Hnrc8nhOSTbnV1Q3O1CPjlX6')

data_path = os.environ.get('GETTY_PIPELINE_INPUT', '/data')
pipeline_tmp_path = os.environ.get('GETTY_PIPELINE_TMP_PATH', '/tmp')
pipeline_common_service_files_path = os.environ.get('GETTY_PIPELINE_COMMON_SERVICE_FILES_PATH', os.path.join(data_path, 'common'))
output_file_path = os.environ.get('GETTY_PIPELINE_OUTPUT', '/data2/output')
DEBUG = os.environ.get('GETTY_PIPELINE_DEBUG', True)
SPAM = os.environ.get('GETTY_PIPELINE_VERBOSE', False)

gpi_engine = 'sqlite:///%s/gpi.sqlite' % (data_path,)
raw_engine = 'sqlite:///%s/raw_gpi.sqlite' % (data_path,)

def project_data_path(project_name):
	return os.path.join(data_path, project_name)

def pipeline_project_service_files_path(project_name):
	path = os.environ.get('GETTY_PIPELINE_SERVICE_FILES_PATH')
	if not path:
		path = os.path.join(data_path, project_name)
	return path
