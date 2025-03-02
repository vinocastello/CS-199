from calendar import monthrange
from datetime import datetime, timedelta
from time import time_ns
from typing import Optional

import numpy as np
import pandas as pd
from classes.classifier import Classifier
from classes.sensor import Sensor
from classes.srp import SensorRetentionPolicy, SRPEvalResult
from classes.utils import LOG, LOG_DUR
from classes.web3client import Web3Client
from pympler import asizeof


class Gateway:
    # This is needed since Ethereum does not have float type.
    PRECISION = 100.0

    def __init__(
            self,
            gateway_id: str,
            srp: Optional[SensorRetentionPolicy],
            classifier: Classifier,
            sensors: list[Sensor],
            web3: Web3Client,
            start_date: datetime,
            end_date: datetime,
            i: str,
            test_case) -> None:
        self.id = gateway_id
        self.srp = srp
        self.classifier = classifier
        self.web3 = web3
        self.sensors = sensors
        self.banned_sensors: list[str] = []
        self.start_date = start_date
        self.end_date = end_date
        self.date = start_date

        # This may refer to the end date of the first batch training or
        # succeeding retraining.
        self.retraining_end_date = \
            self.compute_retraining_end_date()

        self.is_retraining = True
        self.retraining_event_counter = 0

        # Test case number
        self.i = i
        self.test_case = test_case

        self.detection_tracker = {}

    def run(self) -> None:
        while self.date <= self.end_date and len(self.sensors):
            # To separate logs per date
            print('')

            if self.is_finished_retraining() and self.is_retraining:
                self.is_retraining = False

            LOG('Date', self.date)

            messages = [sensor.transmit_data_entry(
                self.date) for sensor in self.sensors]
            processing_start = time_ns()

            # Only inject malicious data when the sensors are not retraining
            if not self.is_retraining:
                self.inject_malicious_data(messages)

            classification_result = []
            if self.is_finished_retraining():
                classification_result = self.classify_data(messages)

                if self.srp is not None:
                    newly_banned_sensors, evaluation_result = self.srp.evaluate_sensors(
                        classification_result, self.date)
                    LOG('newly banned sensors', newly_banned_sensors)
                    LOG('evaluation result', evaluation_result)

                    self.update_detection_tracker(newly_banned_sensors)

                    is_hacked = evaluation_result == SRPEvalResult.HackedSensors
                    self.log_detection_time(newly_banned_sensors, is_hacked)

                    match evaluation_result:
                        case SRPEvalResult.Normal | SRPEvalResult.HackedSensors:
                            self.remove_newly_banned_sensors(
                                newly_banned_sensors)
                        case SRPEvalResult.LegitimateReadingShift:
                            # TODO: Finalize this case.
                            self.retraining_event_counter += 1

                            # NOTE: For now, we will not retrain when there is a
                            #       legitimate reading shift.
                            # retraining_event_counter += 1
                            # self.restart_classifier_training()
                            # self.is_retraining = True
                            # continue
                        case _:
                            pass
                else:
                    self.handle_no_srp(classification_result)

            # Remove messages from banned sensors
            messages = [
                message for message in messages if message['sender'] not in self.banned_sensors]

            # Remove messages from classified malicious sensors
            if len(classification_result):
                classified_malicious_sensors = []
                for sensor_id in classification_result:
                    if classification_result[sensor_id][0] == -1:
                        classified_malicious_sensors.append(sensor_id)

                messages = [
                    message for message in messages if message['sender'] not in classified_malicious_sensors
                ]

            if len(messages):
                sensor_ids, data_entries, date = self.extract_from_messages(
                    messages)
                self.store_data_to_blockchain(sensor_ids, data_entries, date)

            duration = time_ns() - processing_start
            LOG('processing time', duration, self.i, 'nanoseconds')

            self.date += timedelta(days=1)
            if self.is_first_day_of_the_month() and len(self.sensors) and self.is_retraining:
                self.train_new_classifier()

        if self.date > self.end_date:
            print('Ending program since there are no data left to process.')

        if not len(self.sensors):
            print('Ending program since there are no sensors left in the cluster.')

        self.log_modified_fscore_components()
        LOG('Number of retraining', self.retraining_event_counter)

    def sensor_is_malicious_today(self, sensor_id):
        if self.test_case[sensor_id]['atk_date'] != 'None':
            atk_drtn = int(self.test_case[sensor_id]['atk_drtn'])
            atk_date_start = datetime.strptime(
                self.test_case[sensor_id]['atk_date'], '%b %d, %Y')
            atk_date_end = atk_date_start + timedelta(days=atk_drtn-1)

            if self.date == atk_date_start:
                print('{} station starts its attack today!'.format(sensor_id))

            return atk_date_start <= self.date <= atk_date_end
        return False

    def inject_malicious_data(self, messages):
        ''' For simulation only. '''
        for sensor in self.sensors:
            # Insert malicious data if the sensor should be malicious today
            if self.sensor_is_malicious_today(sensor.id):
                malicious_data: pd.Series = sensor.malicious_data.query(
                    f'YEAR == {self.date.year} and MONTH == {self.date.month} and DAY == {self.date.day}'
                ).squeeze().astype(int)
                LOG('malicious_data', malicious_data)

                for i in range(len(messages)):
                    if messages[i]['sender'] == sensor.id:
                        messages[i]['data'] = malicious_data
                        break

    def classify_data(self, messages):
        ''' 
        Get data_entries in messages and classify them. 
        Returns { id: (result, label), ... } 
        '''
        sensor_ids = [message['sender'] for message in messages]

        data_entries = [message['data'] for message in messages]
        data_entries = pd.DataFrame(data_entries)

        data_entries = data_entries[['TMAX', 'TMIN',
                                     'TMEAN', 'RH', 'WIND_SPEED']].to_numpy()

        label = np.full(data_entries.shape[0], 1)

        # Only update items in label to -1 when the sensors are not retraining
        if not self.is_retraining:
            for i, sensor_id in enumerate(sensor_ids):
                if self.sensor_is_malicious_today(sensor_id):
                    label[i] = -1

        LOG('data_entries', data_entries)
        LOG('label', label)

        classification_result = self.classifier.classify(
            data_entries, self.date.month)
        classification_result = list(zip(classification_result, label))

        for res, label in classification_result:
            match (res, label):
                case (1,  1): LOG('tp', 1)
                case (-1, -1): LOG('tn', 1)
                case (1, -1): LOG('fp', 1)
                case (-1,  1): LOG('fn', 1)

        classification_result = dict(zip(sensor_ids, classification_result))

        return classification_result

    def remove_newly_banned_sensors(self, newly_banned_sensors: list[str]):
        self.banned_sensors += newly_banned_sensors
        self.sensors = [
            sensor for sensor in self.sensors if sensor.id not in self.banned_sensors]

    def restart_classifier_training(self):
        # Clear classifiers
        self.classifier.models = [None for _ in range(12)]

        # Set start date and current date to first day of next month
        curr_month = self.date.month
        curr_year = self.date.year
        updated_curr_month = curr_month + 1 if curr_month != 12 else 1
        updated_curr_year = curr_year if curr_month != 12 else curr_year + 1
        self.date = datetime(
            updated_curr_year, updated_curr_month, 1)

        # Set first batch training end date to 1 year after start date.
        self.retraining_end_date = self.compute_retraining_end_date()

    def handle_no_srp(self, classification_result):
        malicious_sensors = []
        for sensor in classification_result:
            if classification_result[sensor][0] == -1:
                malicious_sensors.append(sensor)

        self.remove_newly_banned_sensors(malicious_sensors)

    def extract_from_messages(self, messages):
        sensor_ids = [messages[i]['sender'] for i in range(len(messages))]
        data_entries = [messages[i]['data'] for i in range(len(messages))]
        date = messages[0]['date_sent']

        return sensor_ids, data_entries, date

    def store_data_to_blockchain(self, sensor_ids, data_entries, date):
        data = []
        for data_i in data_entries:
            # We multiply by self.PRECISION to be able to store float number in Ethereum.
            data_i *= self.PRECISION

            tmax = int(data_i['TMAX'])
            tmin = int(data_i['TMIN'])
            tmean = int(data_i['TMEAN'])
            rh = int(data_i['RH'])
            wind_speed = int(data_i['WIND_SPEED'])

            data.append((tmax, tmin, tmean, rh, wind_speed))

        self.web3.store_data_to_blockchain(sensor_ids, date, data)

    def train_new_classifier(self):
        previous_month = 12 if self.date.month == 1 else self.date.month - 1
        year = self.date.year - 1 if self.date.month == 1 else self.date.year

        read_data_start_time = time_ns()
        training_data = self.web3.read_data_from_blockchain(
            previous_month, year)
        LOG_DUR('read data from blockchain', read_data_start_time)

        # Remove all data entries from banned sensors and get only the data part.
        training_data = [
            data for data in training_data if data[0] not in self.banned_sensors]
        training_data = [data[1] for data in training_data]

        # Return back to its original precision.
        training_data = np.array(training_data, dtype=float)
        training_data /= self.PRECISION

        train_start_time = time_ns()
        self.classifier.train(training_data, previous_month)
        LOG_DUR('train classifier', train_start_time)

        if self.classifier.is_complete_models():
            models_size = asizeof.asizeof(self.classifier.models)
            LOG('models memory size', models_size, self.i, 'bytes')

    def compute_retraining_end_date(self) -> datetime:
        ''' 
        This returns the first day of the month a year after the start of
        retraining. 
        '''
        year = self.date.year if self.date.month == 1 else self.date.year + 1
        month = 12 if self.date.month == 1 else self.date.month - 1
        day = monthrange(year, month)[1]
        end_date = datetime(year, month, day)

        return end_date

    def update_detection_tracker(self, banned_sensors):
        for sensor_id in banned_sensors:
            self.detection_tracker[sensor_id] = self.date

    def is_finished_retraining(self) -> bool:
        return self.date > self.retraining_end_date

    def is_first_day_of_the_month(self) -> bool:
        ''' The start date is not included '''
        return self.date.day == 1 and self.date != self.start_date

    def log_detection_time(self, newly_banned_sensors, is_hacked: bool):
        for sensor in newly_banned_sensors:
            attack_date = self.test_case[sensor]['atk_date']

            if attack_date == 'None':
                continue

            attack_date = datetime.strptime(attack_date, '%b %d, %Y')
            if self.date > attack_date or is_hacked:
                if attack_date <= self.retraining_end_date:
                    # When an attack is scheduled during a retraining,
                    # detection time becomes: date of detection - date of end of retraining
                    # since we suppress the attacked sensor from sending malicious
                    # data during the retraining, hence the MNDP can only detect
                    # malicious data after the retraining.
                    detection_time = (
                        self.date - self.retraining_end_date).days
                else:
                    detection_time = (self.date - attack_date).days

                LOG('detection time', detection_time, self.i, 'days')

    def log_modified_fscore_components(self):
        for sensor in self.sensors:
            if self.test_case[sensor.id]['atk_date'] == 'None':
                LOG('tp', sensor.id, self.i)
            else:
                LOG('fp', sensor.id, self.i)

        for sensor in self.banned_sensors:
            if self.test_case[sensor]['atk_date'] == 'None' or self.has_attacked_prematurely(sensor):
                LOG('fn', sensor, self.i)
            else:
                LOG('tn', sensor, self.i)

    # This occurs when a sensor is removed from the cluster before its
    # attack date.
    def has_attacked_prematurely(self, sensor_id: str) -> bool:
        attack_date = datetime.strptime(
            self.test_case[sensor_id]['atk_date'], '%b %d, %Y')
        return (sensor_id in self.detection_tracker) \
            and (self.detection_tracker[sensor_id] < attack_date)
