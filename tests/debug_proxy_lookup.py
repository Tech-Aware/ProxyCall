from repositories.clients_repository import ClientsRepository

if __name__ == "__main__":
    client = ClientsRepository.get_by_proxy_number("+33123456789")
    print("RESULT:", client)
