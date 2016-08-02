import pika
import logging
from esgfpid.utils import check_presence_of_mandatory_args
from esgfpid.utils import add_missing_optional_args_with_value_none
import esgfpid.defaults
import esgfpid.rabbit.rabbitutils
import esgfpid.utils as utils

LOGGER = logging.getLogger(__name__)
LOGGER.addHandler(logging.NullHandler())

def check_pid_queue_availability(**args):
    rabbit_checker = RabbitChecker(**args)
    rabbit_checker.check_and_inform()

class RabbitChecker(object):

    #
    # Init
    #

    def __init__(self, **args):
        mandatory_args = ['messaging_service_username', 'messaging_service_password']
        optional_args = ['messaging_service_url_preferred', 'messaging_service_urls', 'print_to_console']
        check_presence_of_mandatory_args(args, mandatory_args)
        add_missing_optional_args_with_value_none(args, optional_args)
        self.__check_if_any_url_specified(args)
        self.__rename_url_args(args)
        self.__adapt_url_args(args)
        self.__define_all_attributes()
        self.__fill_all_attributes(args)

    def __define_all_attributes(self):
        self.__print_to_console = False
        self.__default_log_level = logging.DEBUG
        self.__error_messages = []
        self.__rabbit_username = None
        self.__rabbit_password = None
        self.__rabbit_hosts = None # all passed hosts that are NOT the current one!
        self.__current_rabbit_host = None # the only host OR the preferred OR a randomly chosen
        self.__exchange_name = None

    def __check_if_any_url_specified(self, args):
        preferred_given = args['messaging_service_url_preferred'] is not None
        other1 = args['messaging_service_urls'] is not None
        other2 = len(args['messaging_service_urls']) > 0
        other_given = (other1 and other2)
        if not (preferred_given or other_given):
            raise esgfpid.exceptions.ArgumentError('At least one messaging service URL has to be specified.') 

    def __rename_url_args(self, args):
        args['urls_fallback'] = args['messaging_service_urls']
        del args['messaging_service_urls']
        args['url_preferred'] = args['messaging_service_url_preferred']
        del args['messaging_service_url_preferred']

    def __adapt_url_args(self, args):
        esgfpid.rabbit.rabbitutils.ensure_urls_are_a_list(args, LOGGER)
        esgfpid.rabbit.rabbitutils.set_preferred_url(args, LOGGER)

    def __fill_all_attributes(self, args):
        self.__rabbit_username = args['messaging_service_username']
        self.__rabbit_password = args['messaging_service_password']
        self.__rabbit_hosts = args['urls_fallback']
        self.__current_rabbit_host = args['url_preferred']
        if args['print_to_console'] is not None and args['print_to_console'] == True:
            self.__print_to_console = True


    #
    # Perform the checks
    #

    def check_and_inform(self):
        self.__loginfo('Checking config for PID module (rabbit messaging queue) ...')
        success = self.__iterate_over_all_hosts()
        if success:
            self.__loginfo('Config for PID module (rabbit messaging queue).. ok.')
            self.__loginfo('Successful connection to PID messaging queue at "%s".' % self.__current_rabbit_host)
        else:
            self.__loginfo('Config for PID module (rabbit messaging queue) .. FAILED!')
            self.__assemble_and_print_error_message()
        return success

    def __iterate_over_all_hosts(self):
        success = False
        print_conn = True
        print_chan = True
        while True:
            try:
                if print_conn:
                    self.__loginfo(' .. checking authentication and connection ...')
                    print_conn = False
                    print_chan = True
                
                connection = self.__check_making_rabbit_connection()

                if print_chan:
                    self.__loginfo(' .. checking authentication and connection ... ok.')
                    self.__loginfo(' .. checking channel ...')
                    print_chan = False
                    print_conn = True

                channel = self.__check_opening_channel(connection)
                success = True

                connection.close()
                break # success, leave loop

            except ValueError as e:

                if self.__is_url_left(): # stay in loop, try next host
                    utils.logtrace(LOGGER, 'Left URLs: %s', self.__rabbit_hosts)
                    self.__set_next_url()
                    utils.logtrace(LOGGER, 'Now trying: %s', self.__current_rabbit_host)

                else: # definitive fail, leave loop
                    break
        return success
            
    def __is_url_left(self):
        if len(self.__rabbit_hosts) > 0:
            return True
        return False

    def __set_next_url(self):
        self.__current_rabbit_host = self.__rabbit_hosts.pop()

    #
    # Building connections:
    #

    def __check_opening_channel(self, connection):
        channel = None
        try:
            self.__open_channel(connection)
            self.__loginfo(' .. checking channel ... ok.')

        except pika.exceptions.ChannelClosed:
            self.__loginfo(' .. checking channel ... FAILED.')
            self.__add_error_message_channel_closed()
            raise ValueError('Channel failed, please try next.')

        return channel

    def __open_channel(self, connection):
        channel = connection.channel()
        channel.confirm_delivery()
        return channel

    def __check_making_rabbit_connection(self):
        connection = None
        try:
            connection = self.__open_rabbit_connection()

        except pika.exceptions.ProbableAuthenticationError:
            self.__loginfo(' .. checking authentication (%s)... FAILED.' % self.__current_rabbit_host)
            self.__add_error_message_authentication_error()
            raise ValueError('Connection failed, please try next.')

        except pika.exceptions.ConnectionClosed:
            self.__loginfo(' .. checking connection (%s)... FAILED.' % self.__current_rabbit_host)
            self.__add_error_message_connection_closed()
            raise ValueError('Connection failed, please try next.')

        if connection is None or not connection.is_open:
            self.__loginfo(' .. checking connection (%s)... FAILED.' % self.__current_rabbit_host)
            self.__add_error_message_connection_problem()
            raise ValueError('Connection failed, please try next.')

        self.__loginfo(' .. checking authentication and connection (%s)... ok.' % self.__current_rabbit_host)
        return connection

    def __open_rabbit_connection(self):
        credentials = pika.PlainCredentials(
            self.__rabbit_username,
            self.__rabbit_password
        )
        params = pika.ConnectionParameters( # https://pika.readthedocs.org/en/0.9.6/connecting.html
            host=self.__current_rabbit_host,
            credentials=credentials
        )
        connection = self.__pika_blocking_connection(params)
        return connection

    def __pika_blocking_connection(self, params): # this is easy to mock
        return pika.BlockingConnection(params)

    #
    # Error messages
    #

    def __add_error_message_general(self):
        self.__error_messages.insert(0,'PROBLEM IN SETTING UP')
        self.__error_messages.insert(1,'RABBIT MESSAGING QUEUE (PID MODULE)')
        self.__error_messages.insert(2, 'CONNECTION TO THE PID MESSAGING QUEUE FAILED DEFINITIVELY:')
        self.__error_messages.append('PLEASE NOTIFY handle@dkrz.de AND INCLUDE THIS ERROR MESSAGE.')

    def __add_error_message_channel_closed(self):
        msg = ' - host "%s": Channel failure.' % self.__current_rabbit_host
        self.__error_messages.append(msg)

    def __add_error_message_authentication_error(self):
        msg = (' - host "%s": Authentication failure (user %s, password %s).' % (
            self.__current_rabbit_host,
            self.__rabbit_username,
            self.__rabbit_password
        ))
        self.__error_messages.append(msg)

    def __add_error_message_connection_closed(self):
        msg = ' - host "%s": Connection failure.' % self.__current_rabbit_host
        self.__error_messages.append(msg)

    def __add_error_message_connection_problem(self):
        msg = ' - host "%s": Unknown connection failure.' % self.__current_rabbit_host
        self.__error_messages.append(msg)

    #
    # Inform at the end
    #

    def __assemble_and_print_error_message(self):
        self.__add_error_message_general()
        error_message_string = utils.format_error_message(self.__error_messages)
        self.__logwarn(error_message_string)

    def __loginfo(self, msg):
        utils.loginfo(LOGGER, msg)

    def __logwarn(self, msg):
        if self.__print_to_console == True:
            print(msg)
        utils.logwarn(LOGGER, msg)

